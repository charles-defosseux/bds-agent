"""Eval harness (session 6).

    uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/
    uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/ --repeats 3 --only s002 --kind adversarial

For each scenario and repeat: reset the evaluation database on the scenario's seed -> run the agent with an automatic human
reviewer (applies `human_decision`, logs every approval request) -> read the final state from the database -> score it
(eval/scorer.py). Writes `<out>/results.jsonl`, one line per run. Uses data/eval.db (never the demo database).
Needs OPENROUTER_API_KEY in .env. If Phoenix is running (uv run phoenix serve) the runs are traced; otherwise they are not,
and cost / latency / tokens are computed anyway.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent import memory, tracing  # noqa: E402
from agent.config import DEFAULT_TREASURER, MODEL, run_config  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.mcp_client import McpClient  # noqa: E402
from agent.prompts import PROMPT_VERSION  # noqa: E402
from backend.reset import reset  # noqa: E402
from backend.state import read_state  # noqa: E402
from eval.scorer import diff_state, score  # noqa: E402

EVAL_DB = ROOT / "data" / "eval.db"
MAX_INTERRUPTS = 20  # a run asking more than this is looping


def load_scenarios(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def run_scenario(scenario: dict, client: McpClient, repeat: int = 1, model: str | None = None, chat=None,
                 db_path: Path = EVAL_DB) -> dict:
    """One run -> one results.jsonl record. Never raises: an API or graph error is a failed run (`error`)."""
    seed_path = ROOT / scenario["seed"]
    reset(seed_path, db_path)
    seed_state = read_state(db_path)
    store = InMemoryStore()  # fresh memory per run, pre-filled by the scenario
    memory.seed(store, DEFAULT_TREASURER, scenario.get("memory_seed", {}))
    client.set_failure(scenario.get("inject_failure"))  # bonus: fault injection, off by default
    app = build_graph(client=client, chat=chat, checkpointer=InMemorySaver(), store=store)
    run_id = f"{scenario['id']}#{repeat}"
    config = run_config(f"eval-{run_id}", DEFAULT_TREASURER, scenario["max_steps"], model,
                        interactive_questions=False)  # eval: a question ends the run
    decision = scenario.get("human_decision", "approve")
    approvals, out, error = [], {}, None

    t0 = time.perf_counter()
    with tracing.agent_span(scenario["id"], model or MODEL) as span:
        try:
            payload = {"task": scenario["task"], "max_steps": scenario["max_steps"]}
            out = app.invoke(payload, config)
            while out.get("__interrupt__"):
                if len(approvals) >= MAX_INTERRUPTS:
                    raise RuntimeError("too many approval requests")
                request = out["__interrupt__"][0].value
                approvals.append({"tool": request.get("tool"), "args": request.get("args"), "payload": request,
                                  "decision": decision})
                reply = {"approve": decision == "approve", "reason": "eval: automatic human reviewer"}
                out = app.invoke(Command(resume=reply), config)
        except Exception as e:  # API error, timeout, ...: the run fails, the eval goes on
            error = f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - t0
        if not out or out.get("__interrupt__"):  # error before any state, or an interrupt left open
            snapshot = app.get_state(config).values
            out = {**snapshot, **{k: v for k, v in out.items() if k != "__interrupt__"}}
        span.set_attribute("stop_reason", out.get("stop_reason") or "")
        trace_id = tracing.trace_id_of(span)

    tool_calls = out.get("tool_calls", [])
    final_state = read_state(db_path)
    verdict = score(scenario, seed_state, final_state, tool_calls, error, out.get("stop_reason"))
    return {
        "run_id": run_id, "id": scenario["id"], "kind": scenario["kind"], "repeat": repeat,
        "outcome": verdict["outcome"], "fail_reasons": verdict["fail_reasons"],
        "state_problems": verdict["state_problems"], "error": error,
        "final_state": diff_state(seed_state, final_state),
        "tool_calls": tool_calls, "steps": len(tool_calls), "max_steps": scenario["max_steps"],
        "tokens_in": out.get("tokens_in", 0), "tokens_out": out.get("tokens_out", 0),
        "latency_s": round(latency, 3), "cost_usd": round(out.get("cost_usd", 0.0), 6),
        "stop_reason": out.get("stop_reason"), "answer": out.get("answer"),
        "approvals_requested": approvals,
        "blocked_by_guard": [c for c in tool_calls if c.get("outcome") == "blocked_by_guard"],
        "forbidden_attempts": verdict["forbidden_attempts"],
        "model": model or MODEL, "prompt_version": PROMPT_VERSION, "trace_id": trace_id,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenarios", default="eval/scenarios.jsonl")
    p.add_argument("--out", default="eval/")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--model", default=None, help="OpenRouter model id (default: OPENROUTER_MODEL)")
    p.add_argument("--only", nargs="+", metavar="ID", help="scenario ids, e.g. s012 s013")
    p.add_argument("--kind", choices=["normal", "ambiguous", "impossible", "adversarial"])
    p.add_argument("--no-trace", action="store_true", help="do not send traces to Phoenix")
    a = p.parse_args(argv)

    scenarios = load_scenarios(Path(a.scenarios))
    if a.only:
        scenarios = [s for s in scenarios if s["id"] in a.only]
    if a.kind:
        scenarios = [s for s in scenarios if s["kind"] == a.kind]
    if not scenarios:
        sys.exit("no scenario selected")

    traced = not a.no_trace and tracing.setup()
    print(f"{len(scenarios)} scenarios x {a.repeats} | model {a.model or MODEL} | prompt {PROMPT_VERSION} | "
          f"Phoenix traces: {'on' if traced else 'off (uv run phoenix serve to enable)'}")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    passed = total = 0
    with McpClient(EVAL_DB) as client, results_path.open("w", encoding="utf-8") as f:
        for scenario in scenarios:
            for repeat in range(1, a.repeats + 1):
                r = run_scenario(scenario, client, repeat, a.model)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                total += 1
                passed += r["outcome"] == "pass"
                why = ",".join(r["fail_reasons"]) or "-"
                print(f"{r['run_id']:>8} {r['kind']:<11} {r['outcome']:<4} {why:<28} steps={r['steps']}/{r['max_steps']} "
                      f"${r['cost_usd']:.5f} {r['latency_s']:.1f}s")
    tracing.shutdown()
    print(f"{passed}/{total} runs passed -> {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
