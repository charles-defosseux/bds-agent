"""Traces (session 7): Phoenix through OpenTelemetry + OpenInference, as in lab 7, with two additions the course asks for:

  * one manual TOOL span per tool call (the OpenAI instrumentor only sees model calls);
  * redaction BEFORE export: "log the shape of the data, never the data". No IBAN, e-mail or API key leaves the process
    (RedactingExporter wraps the real exporter, so it also covers the automatic LLM spans).

Without `setup()` every helper is a no-op: the graph runs the same, and cost / latency / tokens are computed by the harness
without Phoenix.
"""
import re
import socket
from contextlib import contextmanager
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from agent.prompts import PROMPT_VERSION

PHOENIX_URL = "http://localhost:6006"
PROJECT = "bds-agent"

_IBAN = re.compile(r"\b[A-Za-z]{2}\d{2}(?:\s?[A-Za-z0-9]){11,30}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_KEY = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}|\bBearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE)

_provider: TracerProvider | None = None


def redact(text: str) -> str:
    """Replace IBANs, e-mail addresses and API keys by a placeholder."""
    text = _KEY.sub("[API_KEY]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    return _IBAN.sub("[IBAN]", text)


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


class RedactingExporter(SpanExporter):
    """Rebuilds each finished span with redacted attributes and event attributes, then hands it to the real exporter."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans) -> SpanExportResult:
        return self.inner.export([self._redacted(s) for s in spans])

    @staticmethod
    def _redacted(s: ReadableSpan) -> ReadableSpan:
        events = [type(e)(e.name, {k: _clean(v) for k, v in (e.attributes or {}).items()}, e.timestamp) for e in s.events]
        status = s.status
        if status.description:
            status = Status(status.status_code, redact(status.description))
        return ReadableSpan(name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                            attributes={k: _clean(v) for k, v in (s.attributes or {}).items()}, events=events,
                            links=s.links, kind=s.kind, status=status, start_time=s.start_time, end_time=s.end_time,
                            instrumentation_scope=s.instrumentation_scope)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def build_provider(exporter: SpanExporter, project: str = PROJECT) -> TracerProvider:
    """A provider that exports synchronously (batch=False, as in the lab) through the redaction."""
    provider = TracerProvider(resource=Resource.create({"openinference.project.name": project}))
    provider.add_span_processor(SimpleSpanProcessor(RedactingExporter(exporter)))
    return provider


def phoenix_reachable(url: str = PHOENIX_URL) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=0.5):
            return True
    except OSError:
        return False


def setup(url: str = PHOENIX_URL, exporter: SpanExporter | None = None) -> bool:
    """Start tracing (once). Default: OTLP to the local Phoenix; tests pass an in-memory exporter. False if Phoenix is down."""
    global _provider
    if _provider is not None:
        return True
    if exporter is None:
        if not phoenix_reachable(url):
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=f"{url}/v1/traces")
    _provider = build_provider(exporter)
    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument(tracer_provider=_provider)  # one LLM span per model call
    return True


def shutdown() -> None:
    global _provider
    if _provider is not None:
        _provider.force_flush()
        _provider = None


def _tracer():
    return _provider.get_tracer("bds-agent") if _provider else trace.NoOpTracer()


def trace_id_of(span) -> str | None:
    tid = span.get_span_context().trace_id
    return format(tid, "032x") if tid else None


@contextmanager
def agent_span(scenario_id: str, model: str):
    """Root span of one run (kind AGENT). The caller sets `stop_reason` before leaving."""
    with _tracer().start_as_current_span(f"run {scenario_id}") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("scenario.id", scenario_id)
        span.set_attribute("prompt_version", PROMPT_VERSION)
        span.set_attribute("model", model or "")
        yield span


def traced_tool_call(tool: str, args: dict, fn):
    """Run `fn()` (the real tool call) inside a TOOL span. Tool errors are values ({"ok": False, ...}): the span is marked
    ERROR from them; a raised exception is recorded and re-raised."""
    import json
    # The SDK would also record the exception by itself: switched off so it is recorded exactly once, below.
    with _tracer().start_as_current_span(f"tool {tool}", record_exception=False, set_status_on_exception=False) as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", tool)
        span.set_attribute("input.value", json.dumps(args, ensure_ascii=False))
        try:
            result = fn()
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        span.set_attribute("output.value", json.dumps(result, ensure_ascii=False)[:2000])
        if isinstance(result, dict) and result.get("ok") is False:
            err = result.get("error", {})
            span.set_attribute("tool.error_code", str(err.get("code", "")))
            span.set_status(Status(StatusCode.ERROR, f"{err.get('code')}: {err.get('message', '')}"))
        return result
