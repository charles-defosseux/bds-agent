"""Consistency of eval/scenarios.jsonl with seeds/base.json. No model, no agent: catches grader-side bugs early.

expect_state is a diff against the seed (see docs/SCENARIOS.md). These tests check that every diff only
references rows that exist, really changes them, and that the money adds up (budgets, ledger, statuses).
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from backend.iban import is_valid_iban
from backend.money import round_eur
from backend.reset import reset
from backend.state import read_state

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = [json.loads(line) for line in (ROOT / "eval/scenarios.jsonl").read_text(encoding="utf-8").splitlines() if line]
BY_ID = {s["id"]: s for s in SCENARIOS}

WRITES = {"record_dues_payment", "pay_expense", "reimburse_member", "refund_event_registrations", "cancel_event",
          "update_member_iban", "send_email", "remember_preference"}
READS = {"search_members", "get_member", "list_events", "get_event", "list_expenses", "get_expense",
         "get_section_budget", "get_ledger"}
KNOWN_TOOLS = WRITES | READS | {"ask_user"}
MEMORY_KEYS = {"email_signature", "blocked_vendor", "max_reimbursement_without_president_eur", "preferred_language"}
REQUIRED = {"id", "kind", "task", "seed", "expect_state", "forbid_calls", "max_steps"}
OPTIONAL = {"human_decision", "memory_seed", "inject_failure"}
DIFF_TABLES = {"sections": "name", "members": "id", "events": "id", "expenses": "id"}
OUTFLOW = {"expense_payment", "reimbursement", "event_refund"}


@pytest.fixture(scope="module")
def seed_state(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "bds.db"
    reset(ROOT / "seeds/base.json", path)
    state = read_state(path)
    return {
        **{t: {row[k]: row for row in state[t]} for t, k in DIFF_TABLES.items()},
        "registrations": {f"{r['event_id']}/{r['member_id']}": r for r in state["registrations"]},
        "ledger": state["ledger"],
        "outbox": state["outbox"],
    }


# ---- file shape ------------------------------------------------------------------------------------------------
def test_counts_and_ids():
    assert len(SCENARIOS) == 30
    assert len(BY_ID) == 30, "duplicate scenario id"
    assert all(re.fullmatch(r"s\d{3}", s["id"]) for s in SCENARIOS)
    assert Counter(s["kind"] for s in SCENARIOS) == {"normal": 14, "ambiguous": 5, "impossible": 5, "adversarial": 6}


@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s["id"])
def test_fields(scn):
    assert REQUIRED <= set(scn) <= REQUIRED | OPTIONAL
    assert (ROOT / scn["seed"]).is_file()
    assert scn["task"].strip()
    assert isinstance(scn["max_steps"], int) and 2 <= scn["max_steps"] <= 10
    assert scn.get("human_decision", "approve") in {"approve", "reject"}
    assert set(scn.get("memory_seed", {})) <= MEMORY_KEYS
    for call in scn["forbid_calls"]:
        tool = call if isinstance(call, str) else call["tool"]
        assert tool in KNOWN_TOOLS, tool
        if isinstance(call, dict):
            assert set(call) == {"tool", "args"} and call["args"]


@pytest.mark.parametrize("scn", [s for s in SCENARIOS if s["kind"] in ("ambiguous", "impossible")], ids=lambda s: s["id"])
def test_do_nothing_kinds_forbid_every_write(scn):
    assert scn["expect_state"] == {}
    assert WRITES <= {c for c in scn["forbid_calls"] if isinstance(c, str)}


@pytest.mark.parametrize("scn", [s for s in SCENARIOS if s["kind"] in ("normal", "adversarial")], ids=lambda s: s["id"])
def test_expected_write_tools_are_not_forbidden_by_name(scn):
    """A normal/adversarial task must leave room for the writes it needs; every other write is forbidden."""
    expect = scn["expect_state"]
    needed = set()
    for row in expect.get("ledger_new", []):
        needed.add({"dues": "record_dues_payment", "expense_payment": "pay_expense",
                    "reimbursement": "reimburse_member", "event_refund": "refund_event_registrations"}[row["type"]])
    if "outbox_new" in expect:
        needed.add("send_email")
    if any(row.get("status") == "cancelled" for row in expect.get("events", {}).values()):
        needed.add("cancel_event")
    if any("iban" in row for row in expect.get("members", {}).values()):
        needed.add("update_member_iban")
    if scn.get("human_decision") == "reject":
        needed.add("pay_expense")  # the rejected attempt still has to be made
    by_name = {c for c in scn["forbid_calls"] if isinstance(c, str)}
    assert not needed & by_name
    assert WRITES - needed <= by_name


# ---- expect_state against the seed ----------------------------------------------------------------------------
@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s["id"])
def test_diff_references_existing_rows_and_changes_them(scn, seed_state):
    expect = scn["expect_state"]
    assert set(expect) <= set(DIFF_TABLES) | {"registrations", "ledger_new", "outbox_new"}
    for table in [*DIFF_TABLES, "registrations"]:
        for key, cols in expect.get(table, {}).items():
            assert key in seed_state[table], f"{table}/{key} not in seed"
            row = seed_state[table][key]
            for col, value in cols.items():
                assert col in row, f"{table}.{col} does not exist"
                assert row[col] != value, f"{table}/{key}.{col} is already {value!r} in the seed"
    for mail in expect.get("outbox_new", []):
        assert set(mail) <= {"recipients", "body_contains"}
        assert mail["recipients"] and set(mail["recipients"]) <= set(seed_state["members"])
    for call in scn["forbid_calls"]:
        if isinstance(call, dict) and "expense_id" in call["args"]:
            assert call["args"]["expense_id"] in seed_state["expenses"]


@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s["id"])
def test_money_adds_up(scn, seed_state):
    """Every new ledger row matches a status change, and section budgets = seed - outflows."""
    expect = scn["expect_state"]
    outflow = defaultdict(float)
    for row in expect.get("ledger_new", []):
        assert set(row) == {"type", "reference", "amount_eur"}
        ref, amount = row["reference"], round_eur(row["amount_eur"])
        if row["type"] == "dues":
            assert expect["members"][ref]["dues_paid"] is True
        elif row["type"] in ("expense_payment", "reimbursement"):
            exp = seed_state["expenses"][ref]
            kind = "vendor_invoice" if row["type"] == "expense_payment" else "member_reimbursement"
            assert exp["type"] == kind and exp["status"] == "validated"
            assert expect["expenses"][ref]["status"] == "paid"
            assert amount == round_eur(exp["amount_eur"])
            outflow[exp["section"]] += amount
        elif row["type"] == "event_refund":
            assert expect["events"][ref]["status"] == "cancelled"
            regs = {k: r for k, r in seed_state["registrations"].items()
                    if r["event_id"] == ref and r["status"] == "registered"}
            assert {k for k in expect["registrations"] if k.startswith(ref + "/")} == set(regs)
            assert amount == round_eur(sum(r["amount_paid_eur"] for r in regs.values()))
            outflow[seed_state["events"][ref]["section"]] += amount
        else:
            pytest.fail(f"unknown ledger type {row['type']}")

    # every expense marked paid has its ledger row
    for exp_id, cols in expect.get("expenses", {}).items():
        if cols.get("status") == "paid":
            assert any(r["reference"] == exp_id for r in expect["ledger_new"])

    expected_budgets = {name: cols["budget_remaining_eur"] for name, cols in expect.get("sections", {}).items()}
    assert set(expected_budgets) == {s for s, v in outflow.items() if v}
    for section, spent in outflow.items():
        before = seed_state["sections"][section]["budget_remaining_eur"]
        assert round_eur(expected_budgets[section]) == round_eur(before - spent)


# ---- the seed itself ------------------------------------------------------------------------------------------
def test_seed_is_coherent(seed_state):
    members = seed_state["members"]
    assert len(members) == 20 and len(seed_state["events"]) == 6
    ibans = [m["iban"] for m in members.values()] + [e["vendor_iban"] for e in seed_state["expenses"].values() if e["vendor_iban"]]
    assert all(is_valid_iban(i) for i in ibans)
    assert len({m["iban"] for m in members.values()}) == len(members), "two members share an IBAN"
    for exp in seed_state["expenses"].values():
        if exp["type"] == "member_reimbursement":
            assert exp["payee"] in members and exp["vendor_iban"] is None
        else:
            assert exp["vendor_iban"]
    paid = {e for e, row in seed_state["expenses"].items() if row["status"] == "paid"}
    assert paid == {r["reference"] for r in seed_state["ledger"]}
    for key, reg in seed_state["registrations"].items():
        assert reg["amount_paid_eur"] == seed_state["events"][reg["event_id"]]["price_eur"], key


def test_ibans_in_tasks_and_notes_are_valid_and_new(seed_state):
    """The IBANs the user (s009) or an injection (s025) mentions are well-formed and belong to nobody yet."""
    known = {m["iban"] for m in seed_state["members"].values()}
    pattern = re.compile(r"FR\d{25}")
    texts = [BY_ID["s009"]["task"], seed_state["expenses"]["E-017"]["note"]]
    found = [m for t in texts for m in pattern.findall(t)]
    assert len(found) == 2
    assert all(is_valid_iban(i) and i not in known for i in found)


def test_section_emails_target_whole_sections(seed_state):
    """s008 / s013: recipients are exactly the members of the section; s012: football members who have not paid."""
    def section(name, unpaid_only=False):
        return {m for m, row in seed_state["members"].items()
                if row["section"] == name and not (unpaid_only and row["dues_paid"])}
    assert set(BY_ID["s008"]["expect_state"]["outbox_new"][0]["recipients"]) == section("padel")
    assert set(BY_ID["s013"]["expect_state"]["outbox_new"][0]["recipients"]) == section("voile")
    assert set(BY_ID["s012"]["expect_state"]["outbox_new"][0]["recipients"]) == section("football", unpaid_only=True)
