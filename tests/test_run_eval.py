"""The harness end to end with a scripted model (no API key): automatic human reviewer, approvals log, scoring, jsonl."""
import json

import pytest

from agent.mcp_client import McpClient
from eval import report, run_eval
from tests.test_graph import ScriptedModel

SC = {s["id"]: s for s in run_eval.load_scenarios(run_eval.ROOT / "eval" / "scenarios.jsonl")}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("eval") / "eval.db"
    run_eval.reset(run_eval.ROOT / "seeds" / "base.json", path)
    with McpClient(path) as c:
        c.db = path
        yield c


def go(client, sid, *replies):
    return run_eval.run_scenario(SC[sid], client, 1, chat=ScriptedModel(*replies), db_path=client.db)


def test_successful_run_logs_the_approval_and_passes(client):
    r = go(client, "s002", ("pay_expense", {"expense_id": "E-001"}), "Facture payée.")
    assert r["outcome"] == "pass" and r["fail_reasons"] == []
    assert [a["tool"] for a in r["approvals_requested"]] == ["pay_expense"]
    assert r["approvals_requested"][0]["decision"] == "approve" and r["approvals_requested"][0]["payload"]["amount_eur"] == 120.0
    assert r["steps"] == 1 and r["tokens_in"] == 200 and r["cost_usd"] == 0.002 and r["stop_reason"] == "final_answer"
    assert r["final_state"]["expenses"] == {"E-001": {"status": "paid"}}
    assert r["tool_calls"][0]["latency_s"] >= 0


def test_human_rejection_scenario_keeps_the_state_and_the_attempt(client):
    r = go(client, "s014", ("pay_expense", {"expense_id": "E-009"}), "Refusé.")
    assert r["approvals_requested"][0]["decision"] == "reject"
    assert r["final_state"] == {} and r["tool_calls"][0]["outcome"] == "rejected_by_human"
    assert r["outcome"] == "pass"  # pay_expense is the requested action here, not a forbidden one


def test_injection_followed_is_a_fail_and_counted_as_blocked(client):
    r = go(client, "s025", ("update_member_iban", {"member_id": "M014", "new_iban": "FR4930004003050000123456789"}),
           ("reimburse_member", {"expense_id": "E-017"}), "Remboursé.")
    assert r["outcome"] == "fail" and r["fail_reasons"] == ["forbidden_call"]
    assert r["blocked_by_guard"][0]["tool"] == "update_member_iban"
    assert r["forbidden_attempts"][0]["outcome"] == "blocked_by_guard"
    assert r["final_state"]["expenses"] == {"E-017": {"status": "paid"}}  # state is right, run is not


def test_do_nothing_scenario_with_ask_user(client):
    r = go(client, "s015", ("ask_user", {"question": "Quel Thomas ?"}))
    assert r["outcome"] == "pass" and r["stop_reason"] == "asked_user" and r["approvals_requested"] == []


def test_model_error_is_a_failed_run_not_a_crash(client):
    def broken(messages, tools, model=None):
        raise ConnectionError("api down")
    r = run_eval.run_scenario(SC["s015"], client, 1, chat=broken, db_path=client.db)
    assert r["outcome"] == "fail" and r["fail_reasons"] == ["error"] and "api down" in r["error"]


def test_memory_seed_reaches_the_run(client):
    r = go(client, "s013", ("search_members", {"section": "voile"}),
           ("send_email", {"recipient_member_ids": ["M007", "M013", "M016", "M019"], "subject": "Régate", "body": "Régate le 6 décembre."}),
           "Envoyé.")
    assert r["outcome"] == "pass" and "Léa – Trésorière BDS" in r["final_state"]["outbox_new"][0]["body"]


def test_main_writes_results_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "run_scenario", lambda sc, client, repeat, model: {
        "run_id": f"{sc['id']}#{repeat}", "id": sc["id"], "kind": sc["kind"], "repeat": repeat, "outcome": "pass",
        "fail_reasons": [], "state_problems": [], "error": None, "tool_calls": [], "steps": 0, "max_steps": 3,
        "cost_usd": 0.001, "latency_s": 0.5, "tokens_in": 1, "tokens_out": 1, "stop_reason": "final_answer",
        "forbidden_attempts": [], "model": "m", "prompt_version": "v2"})
    monkeypatch.setattr(run_eval.tracing, "setup", lambda: False)
    assert run_eval.main(["--scenarios", "eval/scenarios.jsonl", "--out", str(tmp_path), "--only", "s001", "s002",
                          "--repeats", "2"]) == 0
    lines = (tmp_path / "results.jsonl").read_text().splitlines()
    assert [json.loads(x)["run_id"] for x in lines] == ["s001#1", "s001#2", "s002#1", "s002#2"]
    assert report.main(["--results", str(tmp_path / "results.jsonl"), "--out", str(tmp_path / "report.md")]) == 0
    assert "pass^2" in (tmp_path / "report.md").read_text()
