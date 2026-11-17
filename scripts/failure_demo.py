"""Bonus 2: recovery after an injected failure ("the write goes through but the response is lost").

    uv run python scripts/failure_demo.py            # 3 cases, prints the ledger after each; traces go to Phoenix if it runs

inject_failure = {"tool": "pay_expense", "mode": "timeout_after_write", "times": N}: the first N calls of pay_expense reach
the server and commit, then the client reports a retryable `timeout`. The model is scripted (no API key, no cost): the
point is the harness, not the LLM. Uses a throwaway database (data/failure_demo.db), never data/bds.db.

  A. one lost response   -> tenacity retries with the SAME idempotency key -> the server replays -> 1 ledger line
  B. three lost responses -> retries exhausted, the model sees `timeout` and calls pay_expense again -> same key -> replay
  C. contrast: no idempotency key (random key per attempt) -> the retry hits rule 4 ("already paid"): the ledger still has
     one line, but the agent gets an ERROR for a payment that did happen.
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import agent.mcp_client as mcp_client  # noqa: E402
from agent import tracing  # noqa: E402
from agent.config import run_config  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.mcp_client import McpClient  # noqa: E402
from backend.reset import reset  # noqa: E402
from backend.state import read_state  # noqa: E402

DB = ROOT / "data" / "failure_demo.db"
TASK = "Paie la facture E-001 de Voiles Atlantique."
EXPENSE = {"expense_id": "E-001"}


class Scripted:
    """Plays the model: each reply is (tool, args) or a final answer (str)."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def __call__(self, messages, tools, model=None):
        r = self.replies.pop(0)
        calls = [] if isinstance(r, str) else [{"name": r[0], "arguments": json.dumps(r[1])}]
        return {"content": r if isinstance(r, str) else "", "tool_calls": calls, "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0}


def payments(state: dict) -> list[dict]:
    return [r for r in state["ledger"] if r["reference"] == "E-001"]


def run_case(client: McpClient, name: str, failure: dict, model: Scripted) -> dict:
    reset(ROOT / "seeds" / "base.json", DB)
    client.set_failure(failure)
    thread = f"failure-demo-{name}-{uuid.uuid4().hex[:6]}"
    app = build_graph(client=client, chat=model, checkpointer=InMemorySaver())
    config = run_config(thread, max_steps=6)
    print(f"\n=== Case {name}: inject_failure={json.dumps(failure)} ===")
    with tracing.agent_span(f"failure-demo-{name}", "scripted") as span:
        out = app.invoke({"task": TASK, "max_steps": 6}, config)
        while out.get("__interrupt__"):  # automatic human reviewer: approves every payment request
            out = app.invoke(Command(resume={"approve": True, "reason": "demo"}), config)
        span.set_attribute("stop_reason", out.get("stop_reason") or "")
        trace_id = tracing.trace_id_of(span)
    print("\n".join(out["transcript"]))
    state = read_state(DB)
    lines = payments(state)
    voile = next(s for s in state["sections"] if s["name"] == "voile")["budget_remaining_eur"]
    print(f"--> ledger lines for E-001: {len(lines)} | section voile budget: {voile} EUR | trace_id={trace_id}")
    return {"case": name, "ledger_lines": len(lines), "trace_id": trace_id, "tool_calls": out["tool_calls"]}


def main() -> int:
    phoenix = tracing.setup()
    print(f"Phoenix traces: {'on (http://localhost:6006)' if phoenix else 'off (uv run phoenix serve to enable)'}")
    DB.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with McpClient(DB) as client:
        results.append(run_case(client, "A", {"tool": "pay_expense", "mode": "timeout_after_write", "times": 1},
                                Scripted(("get_expense", EXPENSE), ("pay_expense", EXPENSE), "Facture E-001 payée.")))
        results.append(run_case(client, "B", {"tool": "pay_expense", "mode": "timeout_after_write", "times": 3},
                                Scripted(("get_expense", EXPENSE), ("pay_expense", EXPENSE), ("pay_expense", EXPENSE),
                                         "Facture E-001 payée (une seule fois).")))
        real_key = mcp_client.idempotency_key
        mcp_client.idempotency_key = lambda *a, **k: uuid.uuid4().hex  # a naive client: a new key at every attempt
        try:
            results.append(run_case(client, "C", {"tool": "pay_expense", "mode": "timeout_after_write", "times": 1},
                                    Scripted(("get_expense", EXPENSE), ("pay_expense", EXPENSE),
                                             "Le paiement a échoué (conflit).")))
        finally:
            mcp_client.idempotency_key = real_key
    tracing.shutdown()
    ok = all(r["ledger_lines"] == 1 for r in results)
    print("\nSummary:", ", ".join(f"{r['case']}: {r['ledger_lines']} line" for r in results))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
