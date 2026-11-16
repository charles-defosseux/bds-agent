"""The synchronous MCP client against the two real stdio servers (no model)."""
import pytest

from agent.mcp_client import McpClient, idempotency_key
from backend.reset import reset
from backend.state import read_state
from tests.conftest import BASE_SEED

CTX = {"thread_id": "run-1", "user_id": "M001"}


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return tmp_path_factory.mktemp("mcp") / "bds.db"


@pytest.fixture(scope="module")
def client(db_path):
    reset(BASE_SEED, db_path)
    with McpClient(db_path) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_db(client, db_path):
    reset(BASE_SEED, db_path)  # servers open a connection per call: they see the reset at once


def test_lists_all_tools_from_both_servers(client):
    tools = {t["name"]: t for t in client.list_tools()}
    assert len(tools) == 15
    assert {t["server"] for t in tools.values()} == {"bds-registry", "bds-treasury"}


def test_model_never_sees_hidden_arguments(client):
    for tool in client.list_tools():
        props = tool["input_schema"].get("properties", {})
        assert "idempotency_key" not in props and "actor_id" not in props
        assert not {"idempotency_key", "actor_id"} & set(tool["input_schema"].get("required", []))


def test_schemas_are_constrained_and_classified(client):
    tools = {t["name"]: t for t in client.list_tools()}
    assert "pattern" in tools["pay_expense"]["input_schema"]["properties"]["expense_id"]
    assert "enum" in str(tools["get_section_budget"]["input_schema"])
    assert tools["search_members"]["read_only"] and not tools["pay_expense"]["read_only"]
    assert {n for n, t in tools.items() if t["keyed"]} == {
        "send_email", "record_dues_payment", "pay_expense", "reimburse_member", "refund_event_registrations"}
    assert all("Example" in t["description"] or "example" in t["description"] for t in tools.values())


def test_read_returns_typed_data(client):
    res = client.call("get_member", {"member_id": "M003"}, CTX)
    assert res["ok"] and res["data"]["name"] == "Hugo Bernard"


def test_typed_error_reaches_the_caller(client):
    res = client.call("get_member", {"member_id": "M999"}, CTX)
    assert not res["ok"] and res["error"]["code"] == "not_found" and res["error"]["hint"]


def test_schema_violation_is_invalid_argument(client):
    res = client.call("get_member", {"member_id": "bad"}, CTX)
    assert res["error"]["code"] == "invalid_argument"
    res = client.call("get_member", {"member_id": "M003", "extra": 1}, CTX)
    assert res["error"]["code"] == "invalid_argument" and "extra" in res["error"]["message"]


def test_unknown_tool(client):
    assert client.call("drop_database", {}, CTX)["error"]["code"] == "not_found"


def test_harness_injects_key_and_treasurer(client, db_path):
    res = client.call("record_dues_payment", {"member_id": "M003", "amount_eur": 30}, CTX)
    assert res["ok"]
    row = read_state(db_path)["ledger"][-1]
    assert row["actor_id"] == "M001"
    assert row["idempotency_key"] == idempotency_key("run-1", "record_dues_payment", {"member_id": "M003", "amount_eur": 30})


def test_model_cannot_choose_key_or_actor(client, db_path):
    client.call("pay_expense", {"expense_id": "E-001", "idempotency_key": "chosen", "actor_id": "M999"}, CTX)
    row = read_state(db_path)["ledger"][-1]
    assert row["idempotency_key"] != "chosen" and row["actor_id"] == "M001"


def test_same_call_twice_one_ledger_line(client, db_path):
    a = client.call("pay_expense", {"expense_id": "E-001"}, CTX)
    b = client.call("pay_expense", {"expense_id": "E-001"}, CTX)
    assert a["data"]["replayed"] is False and b["data"]["replayed"] is True
    assert len(read_state(db_path)["ledger"]) == 3


def test_same_action_in_another_run_is_rule_4_not_a_replay(client, db_path):
    client.call("pay_expense", {"expense_id": "E-001"}, CTX)
    res = client.call("pay_expense", {"expense_id": "E-001"}, {**CTX, "thread_id": "run-2"})
    assert res["error"]["code"] == "conflict"
    assert len(read_state(db_path)["ledger"]) == 3


def test_key_is_deterministic_and_depends_on_args():
    a = idempotency_key("t", "pay_expense", {"expense_id": "E-001", "x": 1})
    assert a == idempotency_key("t", "pay_expense", {"x": 1, "expense_id": "E-001"})
    assert a != idempotency_key("t", "pay_expense", {"expense_id": "E-002", "x": 1})
    assert a != idempotency_key("t2", "pay_expense", {"expense_id": "E-001", "x": 1})
