"""Pause -> process stops -> resume from ANOTHER process (session 3). Two real processes, one SQLite checkpoint file.

    uv run python scripts/approval_demo.py               # approve
    uv run python scripts/approval_demo.py --reject      # reject: the ledger does not change

Process 1 resets data/bds.db on seeds/base.json, runs the task with the real model until interrupt() and exits.
Process 2 (new PID, new MCP servers) rebuilds the graph on data/checkpoints.db and resumes the same thread_id.
Needs OPENROUTER_API_KEY in .env.
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TASK = "Paie la facture E-001 de Voiles Atlantique."


def step(name: str, thread_id: str, task: str, approve: bool) -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    from agent.config import CHECKPOINT_DB, run_config
    from agent.graph import build_graph
    from backend.reset import reset
    from backend.state import read_state

    if name == "start":
        reset(ROOT / "seeds" / "base.json")
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    app = build_graph(checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)))
    config = run_config(thread_id)
    if name == "start":
        out = app.invoke({"task": task}, config)
    else:
        out = app.invoke(Command(resume={"approve": approve, "reason": "demo" if approve else "rejected for the demo"}),
                         config)
    print(f"--- process {os.getpid()} ({name}) ---")
    print("\n".join(out.get("transcript", [])))
    if out.get("__interrupt__"):
        print("PAUSED, waiting for a human:", out["__interrupt__"][0].value)
    else:
        print(f"stop_reason={out['stop_reason']} steps={out['steps']} tool_calls={len(out['tool_calls'])} "
              f"tokens={out['tokens_in']}/{out['tokens_out']} cost=${out['cost_usd']:.5f}")
    print(f"ledger lines now: {len(read_state()['ledger'])}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reject", action="store_true")
    p.add_argument("--task", default=TASK)
    p.add_argument("--step", choices=["start", "resume"], help=argparse.SUPPRESS)
    p.add_argument("--thread", help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.step:
        step(a.step, a.thread, a.task, not a.reject)
        return
    thread_id = f"approval-demo-{int(time.time())}"
    print(f"parent process {os.getpid()}, thread_id={thread_id}", flush=True)
    for name in ("start", "resume"):
        cmd = [sys.executable, __file__, "--step", name, "--thread", thread_id, "--task", a.task]
        run = subprocess.run(cmd + (["--reject"] if a.reject else []), cwd=ROOT)
        if run.returncode:
            sys.exit(run.returncode)


if __name__ == "__main__":
    main()
