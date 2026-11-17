"""Redaction before export, and the TOOL span: with an in-memory exporter, no Phoenix needed."""
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import tracing

IBAN = "FR3810278000010002025512345"


@pytest.mark.parametrize("raw,leaked", [
    (f"nouvel IBAN {IBAN}", IBAN),
    ("FR38 1027 8000 0100 0202 5512 345", "1027"),
    ("write to lea.martin@ecole.fr", "lea.martin"),
    ("Authorization: Bearer abcdefghijklmnop1234", "abcdefghijklmnop1234"),
    ("key sk-or-v1-0123456789abcdef", "0123456789abcdef"),
])
def test_redact_removes_sensitive_values(raw, leaked):
    assert leaked not in tracing.redact(raw)


def test_redact_keeps_ordinary_text():
    assert tracing.redact("Paie la facture E-001 de 120 €") == "Paie la facture E-001 de 120 €"


@pytest.fixture
def exporter(monkeypatch):
    exp = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "_provider", tracing.build_provider(exp))
    return exp


def test_exported_spans_never_contain_iban_or_email(exporter):
    with tracing.agent_span("s009", "m") as root:
        root.set_attribute("input.value", f"IBAN {IBAN} de lea@ecole.fr")
        root.add_event("note", {"detail": f"x {IBAN}"})
        tracing.traced_tool_call("update_member_iban", {"new_iban": IBAN}, lambda: {"ok": True, "data": {"to": "a@b.fr"}})
    text = str([(s.attributes, [e.attributes for e in s.events]) for s in exporter.get_finished_spans()])
    assert IBAN not in text and "@" not in text and "[IBAN]" in text


def test_tool_span_kind_attributes_and_error_status(exporter):
    tracing.traced_tool_call("get_expense", {"expense_id": "E-1"}, lambda: {"ok": False, "error": {"code": "not_found", "message": "no"}})
    (span,) = exporter.get_finished_spans()
    assert span.attributes["openinference.span.kind"] == "TOOL" and span.attributes["tool.name"] == "get_expense"
    assert span.attributes["tool.error_code"] == "not_found" and span.status.status_code.name == "ERROR"


def test_tool_span_records_an_exception_and_reraises(exporter):
    def boom():
        raise TimeoutError("server lost")
    with pytest.raises(TimeoutError):
        tracing.traced_tool_call("pay_expense", {}, boom)
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR" and [e.name for e in span.events] == ["exception"]


def test_agent_span_attributes_and_trace_id(exporter):
    with tracing.agent_span("s001", "some/model") as span:
        span.set_attribute("stop_reason", "final_answer")
        tid = tracing.trace_id_of(span)
    (root,) = exporter.get_finished_spans()
    assert root.attributes["openinference.span.kind"] == "AGENT" and root.attributes["scenario.id"] == "s001"
    assert root.attributes["model"] == "some/model" and root.attributes["stop_reason"] == "final_answer"
    assert root.attributes["prompt_version"] and len(tid) == 32


def test_without_setup_everything_is_a_no_op(monkeypatch):
    monkeypatch.setattr(tracing, "_provider", None)
    with tracing.agent_span("s001", "m") as span:
        assert tracing.trace_id_of(span) is None
        assert tracing.traced_tool_call("t", {}, lambda: {"ok": True}) == {"ok": True}
