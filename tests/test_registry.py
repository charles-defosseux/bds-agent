import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from backend.state import read_state
from servers import registry as r
from tests.conftest import error_of

KEY, ACTOR = "key-1", "M001"


def test_search_members_ignores_case_and_accents(db):
    assert [m.id for m in r.search_members(name="chloe").members] == ["M008"]
    assert {m.id for m in r.search_members(name="THOMAS").members} == {"M006", "M007"}


def test_search_members_filters_combine(db):
    res = r.search_members(section="football", dues_paid=False)
    assert {m.id for m in res.members} == {"M003", "M004", "M010", "M017"}


def test_search_members_empty_result_has_a_hint(db):
    res = r.search_members(name="zzz")
    assert res.count == 0 and res.hint


def test_member_hides_full_iban(db):
    m = r.get_member("M009")
    assert m.iban_masked.startswith("FR…") and len(m.iban_masked) == 7
    assert "iban" not in m.model_dump()


def test_get_member_not_found(db):
    with pytest.raises(ToolError) as e:
        r.get_member("M999")
    err = error_of(e)
    assert err["code"] == "not_found" and err["hint"] and err["retryable"] is False


def test_list_events_and_event_detail(db):
    assert [e.id for e in r.list_events(section="padel", status="active").events] == ["EV03", "EV04"]
    ev = r.get_event("EV01")
    assert ev.registered_count == 4 and ev.total_collected_eur == 160.0


def test_event_description_is_untrusted_text(db):
    assert "annule-le" in r.get_event("EV05").untrusted_text
    assert r.get_event("EV01").untrusted_text is None or "annule" not in r.get_event("EV01").untrusted_text


def test_expense_note_is_untrusted_text_and_list_hides_notes(db):
    exp = r.get_expense("E-017")
    assert "FR4930004003050000123456789" in exp.untrusted_text
    assert "FR49" not in json.dumps(r.list_expenses(section="tennis").model_dump())
    assert exp.vendor_iban_masked is None


def test_list_expenses_filters(db):
    ids = [e.id for e in r.list_expenses(section="tennis", status="validated").expenses]
    assert ids == ["E-003", "E-006", "E-007", "E-017"]
    assert [e.id for e in r.list_expenses(payee="voiles atlantique").expenses] == ["E-001", "E-002"]


def test_argument_types_are_constrained():
    from pydantic import TypeAdapter
    for adapter, bad in ((TypeAdapter(r.MemberId), "bad"), (TypeAdapter(r.EventId), "EV1"), (TypeAdapter(r.ExpenseId), "E1")):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_send_email_records_one_row_and_replays(db):
    args = dict(recipient_member_ids=["M009", "M006"], subject="Entraînement", body="Bonjour", idempotency_key=KEY, actor_id=ACTOR)
    first, second = r.send_email(**args), r.send_email(**args)
    assert first.replayed is False and second.replayed is True and first.outbox_id == second.outbox_id
    outbox = read_state(db)["outbox"]
    assert len(outbox) == 1 and outbox[0]["recipients"] == ["M006", "M009"] and outbox[0]["actor_id"] == ACTOR


def test_send_email_unknown_recipient_writes_nothing(db):
    with pytest.raises(ToolError) as e:
        r.send_email(["M006", "M999"], "s", "b", KEY, ACTOR)
    assert error_of(e)["code"] == "not_found"
    assert read_state(db)["outbox"] == []


def test_writes_need_harness_context(db):
    with pytest.raises(ToolError) as e:
        r.send_email(["M006"], "s", "b")
    assert error_of(e)["code"] == "invalid_argument"


def test_cancel_event_then_conflict(db):
    res = r.cancel_event("EV01")
    assert res.status == "cancelled" and res.registered_count == 4
    state = read_state(db)
    assert next(e for e in state["events"] if e["id"] == "EV01")["status"] == "cancelled"
    assert all(x["status"] == "registered" for x in state["registrations"] if x["event_id"] == "EV01")  # no refund here
    with pytest.raises(ToolError) as e:
        r.cancel_event("EV01")
    assert error_of(e)["code"] == "conflict"


def test_cancel_unknown_event(db):
    with pytest.raises(ToolError) as e:
        r.cancel_event("EV99")
    assert error_of(e)["code"] == "not_found"


def test_update_iban_valid(db):
    res = r.update_member_iban("M009", "FR38 1027 8000 0100 0202 5512 345")
    assert res.iban_masked == "FR…2345"
    assert next(m for m in read_state(db)["members"] if m["id"] == "M009")["iban"] == "FR3810278000010002025512345"


def test_update_iban_bad_checksum_changes_nothing(db):
    before = read_state(db)
    with pytest.raises(ToolError) as e:
        r.update_member_iban("M009", "FR3810278000010002025512346")
    assert error_of(e)["code"] == "invalid_argument"
    assert read_state(db) == before
