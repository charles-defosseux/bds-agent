import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.db import connect
from backend.state import read_state
from servers import treasury as t
from tests.conftest import error_of

ACTOR = "M001"


def budget(db, section):
    return next(s for s in read_state(db)["sections"] if s["name"] == section)["budget_remaining_eur"]


def expense(db, eid):
    return next(e for e in read_state(db)["expenses"] if e["id"] == eid)


def fails(code, fn, *args):
    with pytest.raises(ToolError) as e:
        fn(*args)
    err = error_of(e)
    assert err["code"] == code and err["hint"] and err["retryable"] is False
    return err


# ---- reads ------------------------------------------------------------------------------------------------------------
def test_get_section_budget(db):
    assert t.get_section_budget("voile").budget_remaining_eur == 300.0


def test_get_ledger_filters_and_hint(db):
    assert [l.reference for l in t.get_ledger(reference="E-012").lines] == ["E-012"]
    assert t.get_ledger(type="dues").hint


# ---- dues ---------------------------------------------------------------------------------------------------------------
def test_record_dues_payment(db):
    res = t.record_dues_payment("M003", 30, "k1", ACTOR)
    assert (res.type, res.reference, res.amount_eur, res.replayed) == ("dues", "M003", 30.0, False)
    state = read_state(db)
    assert next(m for m in state["members"] if m["id"] == "M003")["dues_paid"] is True
    assert len(state["ledger"]) == 3 and state["ledger"][-1]["actor_id"] == ACTOR
    assert budget(db, "football") == 2000.0  # dues do not touch budgets


def test_dues_already_paid_and_unknown_member(db):
    fails("conflict", t.record_dues_payment, "M001", 30, "k1", ACTOR)
    fails("not_found", t.record_dues_payment, "M999", 30, "k1", ACTOR)


# ---- idempotency --------------------------------------------------------------------------------------------------------
def test_same_key_twice_writes_one_ledger_line(db):
    first = t.pay_expense("E-001", "same", ACTOR)
    second = t.pay_expense("E-001", "same", ACTOR)
    assert first.replayed is False and second.replayed is True and first.ledger_id == second.ledger_id
    assert len(read_state(db)["ledger"]) == 3 and budget(db, "voile") == 180.0  # debited once


def test_other_key_on_paid_invoice_is_rule_4(db):
    t.pay_expense("E-001", "k1", ACTOR)
    fails("conflict", t.pay_expense, "E-001", "k2", ACTOR)
    assert len(read_state(db)["ledger"]) == 3


def test_writes_need_harness_context(db):
    fails("invalid_argument", t.pay_expense, "E-001")


# ---- pay_expense: rules -------------------------------------------------------------------------------------------------
def test_pay_expense_ok(db):
    res = t.pay_expense("E-001", "k1", ACTOR)
    assert (res.type, res.reference, res.amount_eur) == ("expense_payment", "E-001", 120.0)
    assert expense(db, "E-001")["status"] == "paid" and budget(db, "voile") == 180.0


def test_rule_2_exactly_300_needs_no_president(db):
    t.pay_expense("E-004", "k1", ACTOR)
    assert budget(db, "padel") == 100.0


def test_rule_2_above_300_with_president(db):
    t.pay_expense("E-005", "k1", ACTOR)
    assert budget(db, "football") == 1150.0


def test_rule_2_above_300_without_president_is_denied(db):
    fails("permission_denied", t.pay_expense, "E-013", "k1", ACTOR)
    assert expense(db, "E-013")["status"] == "validated" and budget(db, "football") == 2000.0


def test_rule_1_budget_too_low(db):
    err = fails("conflict", t.pay_expense, "E-014", "k1", ACTOR)
    assert "budget" in err["message"].lower()
    assert budget(db, "voile") == 300.0 and expense(db, "E-014")["status"] == "validated"


def test_rule_4_already_paid(db):
    fails("conflict", t.pay_expense, "E-012", "k1", ACTOR)
    assert len(read_state(db)["ledger"]) == 2


def test_rule_5_blocked_vendor(db):
    fails("permission_denied", t.pay_expense, "E-021", "k1", ACTOR)
    assert expense(db, "E-021")["status"] == "validated"


def test_submitted_invoice_is_not_payable(db):
    fails("conflict", t.pay_expense, "E-008", "k1", ACTOR)


def test_rejected_expense_is_not_payable(db):
    conn = connect(db)
    with conn:
        conn.execute("UPDATE expenses SET status = 'rejected' WHERE id = 'E-001'")
    conn.close()
    fails("conflict", t.pay_expense, "E-001", "k1", ACTOR)


def test_pay_expense_wrong_type_and_unknown(db):
    fails("invalid_argument", t.pay_expense, "E-003", "k1", ACTOR)
    fails("not_found", t.pay_expense, "E-999", "k1", ACTOR)


def test_centimes_are_exact(db):
    t.pay_expense("E-006", "k1", ACTOR)
    t.pay_expense("E-007", "k2", ACTOR)
    assert budget(db, "tennis") == 355.1
    assert [l["amount_eur"] for l in read_state(db)["ledger"][-2:]] == [180.0, 64.9]


# ---- reimburse_member ---------------------------------------------------------------------------------------------------
def test_reimburse_member_ok(db):
    res = t.reimburse_member("E-003", "k1", ACTOR)
    assert (res.type, res.amount_eur) == ("reimbursement", 45.0)
    assert expense(db, "E-003")["status"] == "paid" and budget(db, "tennis") == 555.0


def test_rule_3_no_receipt_and_not_validated(db):
    fails("conflict", t.reimburse_member, "E-015", "k1", ACTOR)
    conn = connect(db)
    with conn:  # receipt present but still `submitted`: also refused
        conn.execute("UPDATE expenses SET has_receipt = 1 WHERE id = 'E-015'")
    conn.close()
    fails("conflict", t.reimburse_member, "E-015", "k1", ACTOR)
    assert len(read_state(db)["ledger"]) == 2


def test_reimburse_receipt_missing_but_validated(db):
    conn = connect(db)
    with conn:
        conn.execute("UPDATE expenses SET has_receipt = 0 WHERE id = 'E-003'")
    conn.close()
    err = fails("conflict", t.reimburse_member, "E-003", "k1", ACTOR)
    assert "receipt" in err["message"]


def test_reimburse_already_paid_and_wrong_type(db):
    fails("conflict", t.reimburse_member, "E-019", "k1", ACTOR)
    fails("invalid_argument", t.reimburse_member, "E-001", "k1", ACTOR)


# ---- refund_event_registrations -----------------------------------------------------------------------------------------
def cancel(db, event_id):
    conn = connect(db)
    with conn:
        conn.execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (event_id,))
    conn.close()


def test_refund_requires_cancelled_event(db):
    fails("conflict", t.refund_event_registrations, "EV01", "k1", ACTOR)
    assert len(read_state(db)["ledger"]) == 2


def test_refund_event(db):
    cancel(db, "EV01")
    res = t.refund_event_registrations("EV01", "k1", ACTOR)
    assert (res.type, res.reference, res.amount_eur) == ("event_refund", "EV01", 160.0)
    state = read_state(db)
    assert [r["status"] for r in state["registrations"] if r["event_id"] == "EV01"] == ["refunded"] * 4
    assert budget(db, "voile") == 140.0 and len(state["ledger"]) == 3


def test_refund_twice_with_another_key_is_a_conflict(db):
    cancel(db, "EV01")
    t.refund_event_registrations("EV01", "k1", ACTOR)
    fails("conflict", t.refund_event_registrations, "EV01", "k2", ACTOR)
    assert len(read_state(db)["ledger"]) == 3 and budget(db, "voile") == 140.0


def test_refund_same_key_replays(db):
    cancel(db, "EV02")
    a = t.refund_event_registrations("EV02", "k1", ACTOR)
    b = t.refund_event_registrations("EV02", "k1", ACTOR)
    assert b.replayed and a.ledger_id == b.ledger_id and budget(db, "tennis") == 555.0


def test_refund_budget_too_low_changes_nothing(db):
    cancel(db, "EV01")
    conn = connect(db)
    with conn:
        conn.execute("UPDATE sections SET budget_remaining_eur = 100 WHERE name = 'voile'")
    conn.close()
    fails("conflict", t.refund_event_registrations, "EV01", "k1", ACTOR)
    state = read_state(db)
    assert all(r["status"] == "registered" for r in state["registrations"] if r["event_id"] == "EV01")
    assert len(state["ledger"]) == 2


def test_refund_unknown_event(db):
    fails("not_found", t.refund_event_registrations, "EV99", "k1", ACTOR)


# ---- held-out seed (phase 7): the impossible held-out cases are really refused by the tools -----------------------------
@pytest.fixture
def heldout_db(tmp_path, monkeypatch):
    from pathlib import Path
    from backend.reset import reset
    path = tmp_path / "bds.db"
    reset(Path(__file__).resolve().parents[1] / "seeds" / "heldout.json", path)
    monkeypatch.setenv("BDS_DB_PATH", str(path))
    return path


def test_heldout_impossible_cases_are_refused(heldout_db):
    assert "blocked" in fails("permission_denied", t.pay_expense, "E-024", "k1", ACTOR)["message"]  # h02: vendor blocked
    assert "receipt" in fails("conflict", t.reimburse_member, "E-025", "k2", ACTOR)["message"]      # h03: no receipt
    assert len(read_state(heldout_db)["ledger"]) == 2  # only the two seed lines


# ---- get_expense verdict vs write tools (iteration 2) ------------------------------------------------------------------
@pytest.mark.parametrize("seed", ["seeds/base.json", "seeds/heldout.json", "seeds/heldout_v4.json"])
def test_payable_verdict_agrees_with_the_write_tools_on_every_seed_expense(seed, tmp_path, monkeypatch):
    """`payable` is informational: pay_expense / reimburse_member stay the authority, and the two must never disagree."""
    import json
    from pathlib import Path
    from backend.reset import reset
    from servers import registry as r

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "bds.db"
    monkeypatch.setenv("BDS_DB_PATH", str(path))
    for exp in json.loads((root / seed).read_text(encoding="utf-8"))["expenses"]:
        reset(root / seed, path)  # fresh budgets for every expense
        detail = r.get_expense(exp["id"])
        write = t.pay_expense if exp["type"] == "vendor_invoice" else t.reimburse_member
        try:
            write(exp["id"], f"k-{exp['id']}", ACTOR)
            succeeded = True
        except ToolError:
            succeeded = False
        assert detail.payable == succeeded == (detail.blocking_reasons == []), (exp["id"], detail.blocking_reasons)
