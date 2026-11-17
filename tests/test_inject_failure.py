"""Bonus 2: a lost response after a committed write must not produce a second payment (idempotency key replay)."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.config import run_config
from agent.graph import build_graph
from agent.mcp_client import McpClient
from backend.reset import reset
from backend.state import read_state
from tests.conftest import BASE_SEED
from tests.test_graph import ScriptedModel

PAY = ("pay_expense", {"expense_id": "E-001"})


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "bds.db"
    reset(BASE_SEED, db)
    with McpClient(db) as client:
        yield client, db


def run(client, model, failure, thread="inj"):
    client.set_failure(failure)
    app = build_graph(client=client, chat=model, checkpointer=InMemorySaver())
    config = run_config(thread, max_steps=6)
    out = app.invoke({"task": "Paie la facture E-001.", "max_steps": 6}, config)
    while out.get("__interrupt__"):
        out = app.invoke(Command(resume={"approve": True, "reason": "test"}), config)
    return out


def e001(db):
    return [r for r in read_state(db)["ledger"] if r["reference"] == "E-001"]


@pytest.mark.parametrize("times,replies", [(1, [("get_expense", {"expense_id": "E-001"}), PAY, "ok"]),
                                           (3, [("get_expense", {"expense_id": "E-001"}), PAY, PAY, "ok"])])
def test_lost_response_never_double_pays(env, times, replies):
    client, db = env
    out = run(client, ScriptedModel(*replies), {"tool": "pay_expense", "mode": "timeout_after_write", "times": times})
    assert len(e001(db)) == 1
    last = [o for o in out["observations"] if o["tool"] == "pay_expense"][-1]["result"]
    assert last["ok"] and last["data"]["replayed"] is True


def test_without_injection_nothing_is_replayed(env):
    client, db = env
    out = run(client, ScriptedModel(("get_expense", {"expense_id": "E-001"}), PAY, "ok"), None)
    assert len(e001(db)) == 1
    assert out["observations"][-1]["result"]["data"]["replayed"] is False


def test_unknown_mode_is_refused(env):
    client, _ = env
    with pytest.raises(ValueError):
        client.set_failure({"tool": "pay_expense", "mode": "nope"})
