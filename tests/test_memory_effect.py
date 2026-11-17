"""Memory changes a following run (brief §6): same task, new thread, with and without what a previous run saved.
Real MCP servers, scripted model (no key, no cost). Plus the pure write rules of agent/memory.py."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent import memory
from agent.config import run_config
from agent.graph import build_graph
from agent.mcp_client import McpClient
from backend.reset import reset
from backend.state import read_state
from tests.conftest import BASE_SEED
from tests.test_graph import ScriptedModel

SIGNATURE = "Léa – BDS Treasurer"
REMEMBER_SIG = f'Retiens que je signe mes e-mails "{SIGNATURE}".'
REMEMBER_VENDOR = "Remember: never pay the vendor Sono Événements again."
EMAIL_TASK = "Send an e-mail to the sailing section members to announce the winter regatta on December 6."
PAY_TASK = "Pay invoice E-016 from Sono Événements."
VOILE = ["M007", "M013", "M016", "M019"]


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return tmp_path_factory.mktemp("memory") / "bds.db"


@pytest.fixture(scope="module")
def client(db_path):
    reset(BASE_SEED, db_path)
    with McpClient(db_path) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_db(client, db_path):
    reset(BASE_SEED, db_path)


def run(client, store, model, task, thread, max_steps=4):
    app = build_graph(client=client, chat=model, checkpointer=InMemorySaver(), store=store)
    config = run_config(thread, max_steps=max_steps)
    return app, config, app.invoke({"task": task, "max_steps": max_steps}, config)


def approve(app, config):
    return app.invoke(Command(resume={"approve": True, "reason": "ok"}), config)


def send_voile_email(client, store, thread):
    model = ScriptedModel(("send_email", {"recipient_member_ids": VOILE, "subject": "Winter regatta",
                                          "body": "Hello, the winter regatta is on December 6."}), "Sent.")
    app, config, out = run(client, store, model, EMAIL_TASK, thread)
    assert out["__interrupt__"]  # group e-mail: a human reads it first
    return model, approve(app, config)


def test_a_signature_saved_in_run_1_ends_up_in_the_outbox_of_run_2(client, db_path):
    store = InMemoryStore()
    run1 = ScriptedModel(("remember_preference", {"key": "email_signature", "value": SIGNATURE}), "Noted.")
    _, _, out = run(client, store, run1, REMEMBER_SIG, "thread-A")
    assert out["tool_calls"][0]["outcome"] == "ok"
    assert memory.load(store, "M001") == {"email_signature": SIGNATURE}

    model, _ = send_voile_email(client, store, "thread-B")  # a new thread: only the store carries over
    assert f"KNOWN FACTS (exact, authoritative):\n- email_signature: {SIGNATURE}" in model.seen[0][0]["content"]
    mails = read_state(db_path)["outbox"]
    assert len(mails) == 1 and SIGNATURE in mails[0]["body"]


def test_without_memory_the_same_run_2_sends_an_unsigned_email(client, db_path):
    send_voile_email(client, InMemoryStore(), "thread-B")
    mails = read_state(db_path)["outbox"]
    assert len(mails) == 1 and SIGNATURE not in mails[0]["body"]


def test_a_vendor_blocked_in_run_1_is_not_paid_in_run_2(client, db_path):
    store = InMemoryStore()
    run1 = ScriptedModel(("remember_preference", {"key": "blocked_vendor", "value": "Sono Événements"}), "Noted.")
    run(client, store, run1, REMEMBER_VENDOR, "thread-A")
    before = read_state(db_path)

    # Even a model that ignores KNOWN FACTS cannot pay: the guard stops it before any approval.
    run2 = ScriptedModel(("get_expense", {"expense_id": "E-016"}), ("pay_expense", {"expense_id": "E-016"}),
                         "Je ne paie pas ce prestataire.")
    _, _, out = run(client, store, run2, PAY_TASK, "thread-B")
    assert not out.get("__interrupt__")
    assert [c["outcome"] for c in out["tool_calls"]] == ["ok", "blocked_by_guard"]
    assert read_state(db_path) == before


def test_without_memory_the_same_invoice_is_paid_after_approval(client, db_path):
    model = ScriptedModel(("get_expense", {"expense_id": "E-016"}), ("pay_expense", {"expense_id": "E-016"}), "Paid.")
    app, config, out = run(client, InMemoryStore(), model, PAY_TASK, "thread-B")
    assert out["__interrupt__"]
    approve(app, config)
    assert [e for e in read_state(db_path)["expenses"] if e["id"] == "E-016"][0]["status"] == "paid"


@pytest.mark.parametrize("task, key, value, code", [
    (EMAIL_TASK, "email_signature", "Pirate", "permission_denied"),                      # no "remember" request
    ("Remember that I sign as Léa.", "email_signature", "Ahmed", "permission_denied"),     # not the user's words
    ("Retiens mon IBAN FR3810278000010002025512345.", "email_signature",
     "FR3810278000010002025512345", "permission_denied"),                                # secret
    ("Remember that I like coffee.", "favourite_drink", "coffee", "invalid_argument"),  # key not in the closed list
    ("Retiens le plafond : beaucoup.", "max_reimbursement_without_president_eur", "beaucoup", "invalid_argument"),
])
def test_remember_rules_refuse(task, key, value, code):
    assert memory.check_remember(task, key, value)[0] == code


def test_remember_rules_accept_the_users_exact_words():
    assert memory.check_remember(REMEMBER_SIG, "email_signature", SIGNATURE) is None
    assert memory.check_remember("Retiens : plafond 150 euros", "max_reimbursement_without_president_eur", "150") is None


def test_upsert_keeps_one_entry_per_key_with_history():
    store = InMemoryStore()
    memory.upsert(store, "M001", "email_signature", "A")
    memory.upsert(store, "M001", "email_signature", "A")
    memory.upsert(store, "M001", "email_signature", "B")
    assert memory.load(store, "M001") == {"email_signature": "B"}
    assert [h["value"] for h in store.get(memory.namespace("M001"), "email_signature").value["history"]] == ["A"]
