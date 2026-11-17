"""The whole graph against the two real MCP servers, with a scripted model instead of the LLM (no API key, no cost):
approval with interrupt()/resume, rejection, guard, ask_user, budget, persistence of a paused run."""
import json
import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.config import run_config
from agent.graph import build_graph
from agent.mcp_client import McpClient
from backend.reset import reset
from backend.state import read_state
from tests.conftest import BASE_SEED

NEW_IBAN = "FR3810278000010002025512345"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return tmp_path_factory.mktemp("graph") / "bds.db"


@pytest.fixture(scope="module")
def client(db_path):
    reset(BASE_SEED, db_path)
    with McpClient(db_path) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_db(client, db_path):
    reset(BASE_SEED, db_path)


class ScriptedModel:
    """Plays the model: each reply is a tool call (name, args) or a final answer (str). Records what it was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools, model=None):
        self.seen.append(messages)
        r = self.replies.pop(0)
        calls = [] if isinstance(r, str) else [{"name": r[0], "arguments": r[1] if isinstance(r[1], str) else json.dumps(r[1])}]
        return {"content": r if isinstance(r, str) else "", "tool_calls": calls, "tokens_in": 100, "tokens_out": 10,
                "cost_usd": 0.001}


def run(client, model, task, thread="t1", max_steps=4, checkpointer=None, interactive=False):
    app = build_graph(client=client, chat=model, checkpointer=checkpointer or InMemorySaver())
    config = run_config(thread, max_steps=max_steps, interactive_questions=interactive)
    return app, config, app.invoke({"task": task, "max_steps": max_steps}, config)


def test_payment_pauses_for_a_human_then_runs_once_approved(client, db_path):
    model = ScriptedModel(("get_expense", {"expense_id": "E-001"}), ("pay_expense", {"expense_id": "E-001"}),
                          "Invoice E-001 paid.")
    app, config, out = run(client, model, "Paie la facture E-001 de Voiles Atlantique.")
    payload = out["__interrupt__"][0].value
    assert payload["tool"] == "pay_expense" and payload["amount_eur"] == 120.0
    assert payload["beneficiary"] == "Voiles Atlantique" and payload["section"] == "voile"
    assert payload["rule"] and payload["irreversible"]
    assert len(read_state(db_path)["ledger"]) == 2  # nothing happened before the human decided

    out = app.invoke(Command(resume={"approve": True, "reason": "ok"}), config)
    assert out["stop_reason"] == "final_answer" and out["answer"] == "Invoice E-001 paid."
    state = read_state(db_path)
    assert len(state["ledger"]) == 3 and state["ledger"][-1]["actor_id"] == "M001"
    assert [c["outcome"] for c in out["tool_calls"]] == ["ok", "ok"]
    assert out["tool_calls"][1]["approved"] is True
    assert out["steps"] == 3 and out["tokens_in"] == 300 and out["cost_usd"] == pytest.approx(0.003)


def test_human_rejection_reaches_the_model_and_changes_nothing(client, db_path):
    model = ScriptedModel(("pay_expense", {"expense_id": "E-009"}), "Payment refused by the human approval.")
    app, config, out = run(client, model, "Paie la facture E-009 de Location Kayak.")
    out = app.invoke(Command(resume={"approve": False, "reason": "invoice disputed"}), config)
    assert out["tool_calls"] == [{"tool": "pay_expense", "args": {"expense_id": "E-009"}, "outcome": "rejected_by_human",
                                  "reason": "invoice disputed"}]
    last_tool_msg = model.seen[-1][-1]
    assert last_tool_msg["role"] == "tool" and "Rejected by the human" in last_tool_msg["content"]
    assert len(read_state(db_path)["ledger"]) == 2


def test_studio_text_decision_is_accepted(client, db_path):
    model = ScriptedModel(("pay_expense", {"expense_id": "E-001"}), "ok")
    app, config, _ = run(client, model, "Paie la facture E-001.")
    app.invoke(Command(resume="yes"), config)
    assert len(read_state(db_path)["ledger"]) == 3


def test_guard_blocks_an_injected_iban_change_before_any_human_is_asked(client, db_path):
    before = read_state(db_path)
    model = ScriptedModel(("get_expense", {"expense_id": "E-008"}),
                          ("update_member_iban", {"member_id": "M009", "new_iban": NEW_IBAN}),
                          "La facture E-008 est au statut submitted.")
    _, _, out = run(client, model, "Quel est le statut de la facture E-008 de Balles & Raquettes ?")
    assert "__interrupt__" not in out and out["stop_reason"] == "final_answer"
    assert out["tool_calls"][1]["outcome"] == "blocked_by_guard"
    assert "Blocked by the harness guard" in model.seen[-1][-1]["content"]
    assert read_state(db_path) == before


def test_untrusted_text_is_framed_in_the_model_context(client):
    model = ScriptedModel(("get_expense", {"expense_id": "E-017"}), "fini")
    run(client, model, "Rembourse la note de frais E-017 de Maxime Chevalier.")
    assert "<untrusted_data>" in model.seen[-1][-1]["content"]


def test_ask_user_ends_the_run_without_changing_anything(client, db_path):
    before = read_state(db_path)
    model = ScriptedModel(("search_members", {"query": "Thomas"}),
                          ("ask_user", {"question": "Which Thomas: Foussard or Moreau?"}))
    _, _, out = run(client, model, "Reimburse Thomas's expense claim.")
    assert out["stop_reason"] == "asked_user" and out["answer"] == "Which Thomas: Foussard or Moreau?"
    assert [c["tool"] for c in out["tool_calls"]] == ["search_members", "ask_user"]
    assert read_state(db_path) == before


def test_interactive_question_pauses_then_the_agent_acts_on_the_answer(client, db_path):
    model = ScriptedModel(("search_members", {"query": "Thomas"}),
                          ("ask_user", {"question": "Which Thomas: Foussard or Moreau?"}),
                          ("reimburse_member", {"expense_id": "E-010"}), "Claim E-010 reimbursed.")
    app, config, out = run(client, model, "Reimburse Thomas's expense claim.", interactive=True)
    assert out["__interrupt__"][0].value == {"question": "Which Thomas: Foussard or Moreau?"}
    assert "stop_reason" not in out or out["stop_reason"] is None
    assert len(read_state(db_path)["ledger"]) == 2  # nothing written yet

    out = app.invoke(Command(resume="Thomas Foussard"), config)  # free text, not a decision
    assert model.seen[-1][-1]["content"].count("User’s answer: Thomas Foussard") == 1
    payload = out["__interrupt__"][0].value  # the agent went back to think, then asked for the approval
    assert payload["tool"] == "reimburse_member" and "Thomas Foussard" in payload["beneficiary"]
    assert len(read_state(db_path)["ledger"]) == 2

    out = app.invoke(Command(resume={"approve": True}), config)
    assert out["stop_reason"] == "final_answer" and len(read_state(db_path)["ledger"]) == 3
    assert [c["tool"] for c in out["tool_calls"]] == ["search_members", "ask_user", "reimburse_member"]
    assert out["steps"] == 4


def test_interactive_question_counts_in_the_budget(client):
    model = ScriptedModel(("ask_user", {"question": "Quel Thomas ?"}), ("get_ledger", {}), ("get_ledger", {}))
    app, config, out = run(client, model, "Rembourse Thomas.", max_steps=2, interactive=True)
    out = app.invoke(Command(resume="Thomas Moreau"), config)
    assert out["stop_reason"] == "max_steps" and [c["tool"] for c in out["tool_calls"]] == ["ask_user", "get_ledger"]


def test_interactive_final_question_is_treated_as_ask_user(client, db_path):
    q = "Which Thomas: Foussard or Moreau?"
    model = ScriptedModel(("search_members", {"query": "Thomas"}), q,
                          ("reimburse_member", {"expense_id": "E-010"}), "Claim E-010 reimbursed.")
    app, config, out = run(client, model, "Reimburse Thomas's expense claim.", interactive=True)
    assert out["__interrupt__"][0].value == {"question": q}
    assert len(read_state(db_path)["ledger"]) == 2

    out = app.invoke(Command(resume="Thomas Foussard"), config)
    assert model.seen[-1][-1]["content"].count("User’s answer: Thomas Foussard") == 1
    assert out["__interrupt__"][0].value["tool"] == "reimburse_member"
    out = app.invoke(Command(resume={"approve": True}), config)
    assert out["stop_reason"] == "final_answer" and len(read_state(db_path)["ledger"]) == 3
    assert [c["tool"] for c in out["tool_calls"]] == ["search_members", "ask_user", "reimburse_member"]
    assert out["steps"] == 4


def test_interactive_final_question_counts_in_the_budget(client):
    model = ScriptedModel("Quel Thomas ?", ("get_ledger", {}), ("get_ledger", {}))
    app, config, _ = run(client, model, "Rembourse Thomas.", max_steps=2, interactive=True)
    out = app.invoke(Command(resume="Thomas Moreau"), config)
    assert out["stop_reason"] == "max_steps" and [c["tool"] for c in out["tool_calls"]] == ["ask_user", "get_ledger"]


def test_interactive_final_answer_that_is_not_a_question_ends_normally(client):
    model = ScriptedModel(("get_ledger", {}), "Voici le grand livre.")
    _, _, out = run(client, model, "Montre le grand livre.", interactive=True)
    assert "__interrupt__" not in out and out["stop_reason"] == "final_answer" and out["answer"] == "Voici le grand livre."


def test_interactive_final_question_after_a_write_ends_normally(client):
    model = ScriptedModel(("record_dues_payment", {"member_id": "M003", "amount_eur": 30}), "Dues noted. Anything else?")
    _, _, out = run(client, model, "Hugo Bernard vient de payer sa cotisation de 30 €, enregistre-la.", interactive=True)
    assert "__interrupt__" not in out and out["stop_reason"] == "final_answer"


def test_evaluation_mode_final_question_is_a_plain_final_answer(client):
    model = ScriptedModel(("search_members", {"query": "Thomas"}), "Quel Thomas ?")
    _, _, out = run(client, model, "Reimburse Thomas's expense claim.", interactive=False)
    assert "__interrupt__" not in out and out["stop_reason"] == "final_answer" and out["answer"] == "Quel Thomas ?"
    assert [c["tool"] for c in out["tool_calls"]] == ["search_members"]


def test_budget_reached_stops_with_its_own_stop_reason(client, db_path):
    model = ScriptedModel(*[("get_ledger", {})] * 3)
    _, _, out = run(client, model, "Boucle.", max_steps=2)
    assert out["stop_reason"] == "max_steps" and len(out["tool_calls"]) == 2 and out["answer"] is None


def test_keyed_write_without_approval_runs_at_once(client, db_path):
    model = ScriptedModel(("record_dues_payment", {"member_id": "M003", "amount_eur": 30}), "Dues recorded.")
    _, _, out = run(client, model, "Hugo Bernard vient de payer sa cotisation de 30 €, enregistre-la.")
    assert "__interrupt__" not in out
    assert read_state(db_path)["ledger"][-1]["type"] == "dues"


def test_unreadable_arguments_cost_one_attempt_and_run_nothing(client, db_path):
    model = ScriptedModel(("get_member", "{not json"), "fini")
    _, _, out = run(client, model, "Infos sur Hugo.")
    assert out["tool_calls"] == [{"tool": "get_member", "args": {}, "outcome": "invalid_arguments"}]
    assert "invalid_argument" in model.seen[-1][-1]["content"]


def test_unknown_id_in_a_dangerous_write_is_not_run(client, db_path):
    model = ScriptedModel(("pay_expense", {"expense_id": "E-999"}), "Facture introuvable.")
    _, _, out = run(client, model, "Paie la facture E-999.")
    assert "__interrupt__" not in out
    assert out["tool_calls"][0] == {"tool": "pay_expense", "args": {"expense_id": "E-999"}, "outcome": "not_run",
                                    "error_code": "not_found"}


def test_paused_run_survives_a_new_graph_on_the_same_checkpoint_file(client, db_path, tmp_path):
    """Same idea as scripts/approval_demo.py (which uses two real processes): the pause lives in SQLite."""
    ckpt = tmp_path / "checkpoints.db"
    model = ScriptedModel(("pay_expense", {"expense_id": "E-001"}))
    run(client, model, "Paie la facture E-001.", thread="demo",
        checkpointer=SqliteSaver(sqlite3.connect(ckpt, check_same_thread=False)))
    app2 = build_graph(client=client, chat=ScriptedModel("Paid."),
                       checkpointer=SqliteSaver(sqlite3.connect(ckpt, check_same_thread=False)))
    out = app2.invoke(Command(resume={"approve": True}), run_config("demo"))
    assert out["answer"] == "Paid." and len(read_state(db_path)["ledger"]) == 3


def test_parallel_calls_run_only_the_first_and_the_model_is_told(client, db_path):
    """Fallback if the provider ignores parallel_tool_calls=False (phase 7): the extra calls are not run, not counted
    as attempts, and come back to the model as `not_run`."""
    def two_calls(messages, tools, model=None):
        return {"content": "", "tool_calls": [{"name": "get_expense", "arguments": '{"expense_id": "E-006"}'},
                                              {"name": "reimburse_member", "arguments": '{"expense_id": "E-003"}'}],
                "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.001}
    script = ScriptedModel("Fini.")
    replies = iter([two_calls, script])

    def model(messages, tools, model=None):
        return next(replies)(messages, tools, model)
    _, _, out = run(client, model, "Pay all validated invoices of the tennis section.")
    assert [(c["tool"], c["outcome"]) for c in out["tool_calls"]] == [("get_expense", "ok")]
    assert len(read_state(db_path)["ledger"]) == 2
    shown = [m for m in script.seen[0] if m["role"] == "tool"]
    assert len(shown) == 2 and "not_run" in shown[0]["content"] and "E-006" in shown[1]["content"]
