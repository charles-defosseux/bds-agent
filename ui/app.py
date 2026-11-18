"""Small demo UI (bonus): plan, tool calls and approval step of the BDS treasury agent.

    uv run streamlit run ui/app.py        (from the project folder; needs OPENROUTER_API_KEY in .env)

It only drives the existing graph, like studio.py and scripts/approval_demo.py: same SqliteSaver checkpointer, one new
thread_id per task, resume after interrupt() with the same payloads ({"approve", "reason"} / free-text answer).
Nothing in agent/, servers/, backend/ or eval/ is changed. LangGraph Studio remains the documented fallback.
"""
import html
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.config import CHECKPOINT_DB, DEFAULT_MAX_STEPS, run_config  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from backend.reset import reset  # noqa: E402
from backend.state import read_state  # noqa: E402

# Tested by hand: they work in this order after a single reset. They only pre-fill the text box.
EXAMPLES = [
    "How many padel invoices are still unpaid?",
    "Pay invoice E-004 from Padel Pro.",
    "Pay invoice E-013 from Bus Évasion.",
    "Reimburse Thomas’s expense claim.",
    "Reimburse expense claim E-017 from Maxime Chevalier.",
    "Cancel the sailing trip to Quiberon on December 12, refund the registrants and notify them by e-mail.",
]
BADGES = {"ok": "#1a7f37", "error": "#9a6700", "blocked_by_guard": "#cf222e", "rejected_by_human": "#cf222e",
          "not_run": "#6e7781", "invalid_arguments": "#9a6700"}
CSS = """
<style>
.badge{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;font-size:.75rem;margin-left:6px}
.call{border-left:3px solid #d0d7de;padding:2px 0 2px 12px;margin:0 0 10px 0}
.call code{font-size:.8rem}
.muted{color:#6e7781;font-size:.82rem;word-break:break-word}
.warn{background:#ffebe9;border:1px solid #cf222e;border-radius:6px;padding:10px 14px;color:#82071e;margin:8px 0}
</style>
"""


@st.cache_resource
def get_app():
    """One graph (and one pair of MCP servers) per Streamlit process; same checkpointer file as the demo script."""
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    return build_graph(checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)))


# ---- pure helpers -------------------------------------------------------------------------------------------------
def short(value, n: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= n else text[:n] + "…"


def timeline(values: dict) -> list[dict]:
    """Every tool-call attempt with its result: matches each attempt with the next unused observation of the same call."""
    observations, used, rows = values.get("observations", []), set(), []
    for call in values.get("tool_calls", []):
        result = None
        for i, obs in enumerate(observations):
            if i not in used and obs["tool"] == call["tool"] and obs["args"] == call["args"]:
                used.add(i)
                result = obs["result"]
                break
        rows.append({**call, "result": result})
    return rows


def result_summary(row: dict) -> str:
    result = row.get("result")
    if row["outcome"] == "rejected_by_human":
        return f"rejected: {row.get('reason') or 'no reason given'}"
    if result is None:
        return row.get("error_code", "")
    if isinstance(result, dict) and result.get("ok") is False:
        err = result.get("error", {})
        return short(f"{err.get('code', 'error')}: {err.get('message', '')}")
    return short(result.get("data", result) if isinstance(result, dict) else result)


def pending_interrupt(snapshot):
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


# ---- rendering ----------------------------------------------------------------------------------------------------
def render_timeline(box, values: dict, max_steps: int) -> None:
    rows = timeline(values)
    with box.container():
        st.subheader("Run timeline")
        used = len(rows)
        st.progress(min(used / max_steps, 1.0) if max_steps else 0.0, text=f"Tool calls: {used} / {max_steps}")
        if not rows:
            st.caption("No tool call yet.")
        for i, row in enumerate(rows, 1):
            color = BADGES.get(row["outcome"], "#6e7781")
            st.markdown(
                f'<div class="call"><b>{i}. {html.escape(row["tool"])}</b>'
                f'<span class="badge" style="background:{color}">{html.escape(row["outcome"])}</span><br>'
                f'<code>{html.escape(short(row["args"], 300))}</code><br>'
                f'<span class="muted">{html.escape(result_summary(row))}</span></div>', unsafe_allow_html=True)


def render_sidebar(box) -> None:
    with box.container():
        st.header("Section budgets")
        try:
            for row in read_state()["sections"]:
                st.metric(row["name"].capitalize(), f"€{row['budget_remaining_eur']:,.2f}")
        except Exception as exc:  # database not created yet
            st.caption(f"No data yet ({exc}). Use Reset demo data.")
        st.button("Reset demo data", on_click=reset_demo, disabled=bool(st.session_state.get("paused")),
                  help="Same as: uv run python -m backend.reset seeds/base.json")
        if st.session_state.get("paused"):
            st.caption("Disabled while the run waits for a human.")


def render_approval(payload: dict) -> None:
    with st.container(border=True):
        st.subheader("Approval required")
        if payload.get("warning"):
            st.markdown(f'<div class="warn"><b>Warning</b><br>{html.escape(str(payload["warning"]))}</div>',
                        unsafe_allow_html=True)
        st.markdown(f"**Action:** {payload.get('action') or payload.get('tool')}")
        amount = payload.get("amount_eur")
        cols = st.columns(3)
        cols[0].metric("Amount", f"€{amount:,.2f}" if amount is not None else "—")
        cols[1].metric("Beneficiary", payload.get("beneficiary") or "—")
        cols[2].metric("Section", payload.get("section") or "—")
        if payload.get("rule"):
            st.caption(f"Rule: {payload['rule']}")
        if payload.get("irreversible"):
            st.caption(f"Irreversible: {payload['irreversible']}")
        with st.expander("Tool call"):
            st.json({"tool": payload.get("tool"), "args": payload.get("args")})
        reason = st.text_input("Reason (used if you reject)", key="reject_reason")
        ok, no = st.columns(2)
        ok.button("Approve", type="primary", use_container_width=True, on_click=resume,
                  args=({"approve": True, "reason": "approved in the UI"},))
        no.button("Reject", use_container_width=True, on_click=resume,
                  args=(None,), kwargs={"reject": True})


def render_question(payload: dict) -> None:
    with st.container(border=True):
        st.subheader("The agent asks a question")
        st.markdown(payload.get("question", ""))
        st.text_input("Your answer", key="answer_text")
        st.button("Send answer", type="primary", on_click=resume, kwargs={"answer": True})


def render_final(values: dict) -> None:
    with st.container(border=True):
        st.subheader("Result")
        st.markdown(values.get("answer") or "_No answer (the run stopped before a final answer)._")
        cols = st.columns(4)
        cols[0].metric("Stop reason", values.get("stop_reason") or "—")
        cols[1].metric("Tokens in / out", f"{values.get('tokens_in', 0):,} / {values.get('tokens_out', 0):,}")
        cols[2].metric("Cost", f"${values.get('cost_usd', 0.0):.5f}")
        cols[3].metric("Latency", f"{st.session_state.get('elapsed', 0.0):.1f} s")
        st.caption("Latency excludes the time spent waiting for a human.")


# ---- callbacks (they run before the page is redrawn) ---------------------------------------------------------------
def prefill(text: str) -> None:
    st.session_state.task = text


def start() -> None:
    task = st.session_state.get("task", "").strip()
    if not task:
        return
    st.session_state.update(thread_id=f"ui-{int(time.time() * 1000)}", elapsed=0.0, paused=False, error=None,
                            max_used=int(st.session_state.max_steps), todo={"task": task, "max_steps": int(st.session_state.max_steps)})


def resume(decision: dict | None = None, reject: bool = False, answer: bool = False) -> None:
    if reject:
        decision = {"approve": False, "reason": st.session_state.get("reject_reason", "").strip() or "rejected in the UI"}
    if answer:
        decision = st.session_state.get("answer_text", "").strip()
        if not decision:
            return
    st.session_state.todo = Command(resume=decision)
    st.session_state.paused = False


def reset_demo() -> None:
    reset(ROOT / "seeds" / "base.json")
    st.session_state.notice = "Demo data reset (seeds/base.json)."


# ---- page ---------------------------------------------------------------------------------------------------------
st.set_page_config(page_title="BDS treasury agent", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.title("BDS treasury agent")
st.caption("One agent, two MCP servers. Money out and group e-mails wait for a human decision.")
st.session_state.setdefault("task", "")
st.session_state.setdefault("max_steps", DEFAULT_MAX_STEPS)
sidebar_box = st.sidebar.empty()
if st.session_state.pop("notice", None):
    st.toast("Demo data reset.")

st.text_area("Task", key="task", height=90, placeholder="Type a task for the treasury agent…")
st.caption("Examples (they only fill the box; run them in this order after one reset):")
for i, example in enumerate(EXAMPLES, 1):
    st.button(f"{i}. {example}", key=f"ex{i}", on_click=prefill, args=(example,))
left, right = st.columns([1, 4])
left.number_input("Max steps (tool calls)", min_value=1, max_value=30, step=1, key="max_steps")
right.write("")
right.button("Run", type="primary", on_click=start)
st.divider()

timeline_box = st.empty()
app = get_app()
thread_id = st.session_state.get("thread_id")
config = run_config(thread_id, max_steps=st.session_state.get("max_used", DEFAULT_MAX_STEPS)) if thread_id else None
max_used = st.session_state.get("max_used", DEFAULT_MAX_STEPS)

todo = st.session_state.pop("todo", None)
if todo is not None:
    started = time.monotonic()
    try:
        with st.spinner("The agent is working…"):
            for _ in app.stream(todo, config, stream_mode="updates"):
                render_timeline(timeline_box, app.get_state(config).values, max_used)
    except Exception as exc:
        st.session_state.error = f"{type(exc).__name__}: {exc}"
    st.session_state.elapsed = st.session_state.get("elapsed", 0.0) + time.monotonic() - started

if config:
    snapshot = app.get_state(config)
    values = snapshot.values or {}
    render_timeline(timeline_box, values, max_used)
    payload = pending_interrupt(snapshot)
    st.session_state.paused = payload is not None
    if st.session_state.get("error"):
        st.error(st.session_state.error)
    elif payload is not None and "question" in payload and "tool" not in payload:
        render_question(payload)
    elif payload is not None:
        render_approval(payload)
    elif values.get("status") == "done":
        render_final(values)
else:
    timeline_box.caption("Pick an example or type a task, then press Run.")

render_sidebar(sidebar_box)
