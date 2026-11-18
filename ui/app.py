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
from agent.tools import READ_TOOLS  # noqa: E402

# Tested by hand: they work in this order after a single reset. They only pre-fill the text box.
EXAMPLES = [
    "How many padel invoices are still unpaid?",
    "Pay invoice E-004 from Padel Pro.",
    "Pay invoice E-013 from Bus Évasion.",
    "Reimburse Thomas’s expense claim.",
    "Reimburse expense claim E-017 from Maxime Chevalier.",
    "Cancel the sailing trip to Quiberon on December 12, refund the registrants and notify them by e-mail.",
]
CHIPS = ["Padel invoices", "Pay E-004", "Pay E-013", "Thomas", "E-017", "Cancel sailing trip"]  # same order as EXAMPLES
NAVY, PAPER, SAND, ORANGE, RUST = "#13213A", "#F6F4EE", "#ECE6D8", "#F2762E", "#B04512"
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;600&family=Public+Sans:wght@400;500;600&display=swap');
:root{--navy:#13213A;--paper:#F6F4EE;--sand:#ECE6D8;--orange:#F2762E;--rust:#B04512;
 --title:'Oswald','Arial Narrow','Helvetica Neue',Arial,sans-serif;
 --text:'Public Sans',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif}
.stApp,.stMarkdown,p,label,button,input,textarea,[data-testid="stMetric"]{font-family:var(--text)}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDeployButton"],[data-testid="stStatusWidget"]{display:none!important}
header[data-testid="stHeader"]{background:transparent}
.block-container{max-width:1120px;padding-top:1.2rem;padding-bottom:4rem}
/* header band */
.band{background:var(--navy);color:var(--paper);border-radius:14px;padding:22px 28px;margin-bottom:22px}
.band h1{font-family:var(--title);font-weight:600;letter-spacing:.02em;font-size:2.1rem;margin:0;padding:0;color:var(--paper)}
.band p{margin:4px 0 12px 0;color:#C9CFDA;font-size:.95rem}
.pill{display:inline-block;border:1px solid rgba(246,244,238,.35);color:var(--paper);border-radius:999px;
 padding:2px 12px;font-size:.74rem;margin-right:8px;letter-spacing:.02em}
h2,h3,.title{font-family:var(--title);font-weight:500;letter-spacing:.02em;color:var(--navy)}
.sec{font-family:var(--title);font-size:1.15rem;letter-spacing:.04em;text-transform:uppercase;color:var(--navy);margin:6px 0 10px 0}
/* buttons */
.stButton button{border-radius:10px;font-weight:600}
.stButton button[kind="primary"]{background:var(--orange);border:1px solid var(--orange);color:#fff}
.stButton button[kind="primary"]:hover{background:#DD6420;border-color:#DD6420;color:#fff}
.stButton button[kind="secondary"]{background:transparent;border:1.5px solid var(--navy);color:var(--navy)}
.stButton button[kind="secondary"]:hover{background:var(--sand);border-color:var(--navy);color:var(--navy)}
.stButton button[kind="tertiary"]{color:#6B7488;font-weight:500;font-size:.8rem}
div.st-key-chips .stButton button{border-radius:999px!important;border:1px solid #C9BFA6!important;background:var(--sand)!important;color:var(--navy)!important;
 font-weight:500;font-size:.82rem;padding:2px 14px;min-height:2rem}
div.st-key-chips .stButton button:hover{border-color:var(--orange)!important;background:#F6E2D3!important}
div.st-key-chips .stButton button p{white-space:nowrap;font-size:.82rem}
.st-key-taskrow textarea{background:#fff;border:1px solid #C9BFA6;border-radius:10px;color:var(--navy)}
.st-key-taskrow input{background:#fff;border-radius:10px;color:var(--navy)}
/* stepper */
.st-key-approval,.st-key-question,.st-key-final{margin-top:10px}
.stepper{position:relative;margin:8px 0 6px 14px;padding-left:34px;border-left:2px solid #D8D0BD}
.step{position:relative;background:#fff;border:1px solid #E3DCCB;border-radius:12px;padding:10px 14px;margin-bottom:10px}
.dot{position:absolute;left:-52px;top:10px;width:30px;height:30px;border-radius:50%;color:#fff;font-weight:600;font-size:.85rem;
 display:flex;align-items:center;justify-content:center;border:3px solid var(--paper);box-sizing:border-box;
 font-family:var(--title)}
.step .top{display:flex;align-items:center;gap:8px}
.step .name{font-weight:700;color:var(--navy);font-size:.98rem}
.kind{font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;color:#6B7488;border:1px solid #D8D0BD;border-radius:6px;padding:0 6px}
.badge{margin-left:auto;display:inline-block;padding:1px 10px;border-radius:999px;color:#fff;font-size:.72rem;font-weight:600}
.step details{margin-top:6px}
.step summary{cursor:pointer;color:#6B7488;font-size:.8rem}
.step pre{background:var(--paper);border-radius:8px;padding:8px 10px;margin:6px 0 0 0;font-size:.76rem;white-space:pre-wrap;
 word-break:break-word;color:var(--navy)}
.step .lbl{font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;color:#6B7488;margin-top:8px}
.bar{height:6px;border-radius:999px;background:#DDD5C3;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--navy);border-radius:999px}
.barlbl{display:flex;justify-content:space-between;font-size:.8rem;color:var(--navy);margin-bottom:4px}
/* hero cards (approval, question, result) */
.st-key-approval,.st-key-question{background:var(--sand);border:2px solid var(--orange)!important;border-radius:16px;padding:12px 8px}
.st-key-final{background:#fff;border:1px solid #E3DCCB!important;border-radius:16px;padding:12px 8px}
.hero-title{font-family:var(--title);font-size:.95rem;letter-spacing:.1em;text-transform:uppercase;color:var(--orange);margin:0 0 4px 0}
.amount{font-family:var(--title);font-weight:600;font-size:3.2rem;line-height:1.05;color:var(--navy);margin:0}
.meta{display:flex;gap:36px;margin:10px 0 6px 0;flex-wrap:wrap}
.meta .k{font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:#6B7488}
.meta .v{font-size:1.15rem;font-weight:600;color:var(--navy)}
.action{background:#fff;border:1px solid #E3C9B4;border-radius:10px;padding:10px 14px;margin:8px 0;color:var(--navy);font-weight:600}
.small{color:#4A5368;font-size:.82rem;margin:2px 0}
.alert{background:#FBE9DF;border:1.5px solid var(--rust);border-radius:10px;padding:10px 14px;color:var(--rust);margin:10px 0;font-weight:500}
.question{font-family:var(--title);font-weight:500;font-size:1.7rem;line-height:1.3;color:var(--navy);margin:4px 0 8px 0}
.answer{color:var(--navy);font-size:1.02rem;line-height:1.55}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}
.tile{background:var(--sand);border-radius:12px;padding:12px 14px}
.tile .k{font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:#6B7488}
.tile .v{font-family:var(--title);font-weight:500;font-size:1.5rem;color:var(--navy)}
.stopbadge{display:inline-block;background:var(--navy);color:var(--paper);border-radius:999px;padding:2px 12px;font-size:.75rem;font-weight:600}
@media (max-width:760px){.tiles{grid-template-columns:repeat(2,1fr)}.amount{font-size:2.4rem}}
/* sidebar */
section[data-testid="stSidebar"]{background:var(--sand)}
.sb-title{font-family:var(--title);letter-spacing:.06em;text-transform:uppercase;color:var(--navy);font-size:1.05rem;margin-bottom:14px}
.sb-row{margin-bottom:16px}
.sb-row .top{display:flex;justify-content:space-between;color:var(--navy);margin-bottom:4px}
.sb-row .n{font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;font-weight:600}
.sb-row .a{font-family:var(--title);font-size:1.25rem}
.sb-row .bar{background:#D6CDB8}
</style>
"""

OUTCOME_COLORS = {"ok": "#2F7D4F", "error": "#B7791F", "blocked_by_guard": RUST, "rejected_by_human": RUST,
                  "not_run": "#6B7488", "invalid_arguments": "#B7791F"}


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
def esc(value) -> str:
    return html.escape(str(value))


def bar(fraction: float, color: str = NAVY) -> str:
    return f'<div class="bar"><i style="width:{max(0.0, min(fraction, 1.0)) * 100:.0f}%;background:{color}"></i></div>'


def kind_of(tool: str) -> tuple[str, str, str]:
    """(label, dot colour, glyph): read tools navy, write tools orange, control tools grey."""
    if tool in READ_TOOLS:
        return "read", NAVY, "R"
    if tool in ("ask_user", "remember_preference"):
        return "control", "#8A93A6", "?"
    return "write", ORANGE, "W"


def render_header() -> None:
    st.markdown(
        '<div class="band"><h1>BDS Treasury Agent</h1>'
        '<p>One agent plans, calls tools and waits for your approval before money moves.</p>'
        '<span class="pill">2 MCP servers</span><span class="pill">15 tools</span>'
        '<span class="pill">human approval</span></div>', unsafe_allow_html=True)


def render_timeline(box, values: dict, max_steps: int) -> None:
    rows = timeline(values)
    used = len(rows)
    full = max_steps and used >= max_steps
    with box.container():
        st.markdown('<div class="sec">Run timeline</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="barlbl"><span>Tool calls</span><span><b>{used} / {max_steps}</b></span></div>'
                    + bar(used / max_steps if max_steps else 0.0, RUST if full else NAVY), unsafe_allow_html=True)
        if not rows:
            st.caption("No tool call yet.")
            return
        steps = []
        for i, row in enumerate(rows, 1):
            kind, color, _ = kind_of(row["tool"])
            outcome = row["outcome"]
            if outcome != "ok":
                color = RUST if outcome in ("blocked_by_guard", "rejected_by_human") else color
            result = row.get("result")
            result_text = (json.dumps(result, ensure_ascii=False, indent=1) if result is not None
                           else result_summary(row))
            steps.append(
                f'<div class="step"><div class="dot" style="background:{color}">{i}</div>'
                f'<div class="top"><span class="name">{esc(row["tool"])}</span><span class="kind">{kind}</span>'
                f'<span class="badge" style="background:{OUTCOME_COLORS.get(outcome, "#6B7488")}">{esc(outcome)}</span></div>'
                f'<details><summary>Arguments and result</summary>'
                f'<div class="lbl">Arguments</div><pre>{esc(json.dumps(row["args"], ensure_ascii=False, indent=1))}</pre>'
                f'<div class="lbl">Result</div><pre>{esc(short(result_text, 1800))}</pre></details></div>')
        st.markdown(f'<div class="stepper">{"".join(steps)}</div>', unsafe_allow_html=True)


def initial_budgets() -> dict:
    try:
        seed = json.loads((ROOT / "seeds" / "base.json").read_text(encoding="utf-8"))
        return {s["name"]: float(s["budget_remaining_eur"]) for s in seed.get("sections", [])}
    except Exception:
        return {}


def render_sidebar(box) -> None:
    with box.container():
        st.markdown('<div class="sb-title">Section budgets</div>', unsafe_allow_html=True)
        try:
            start_budget = initial_budgets()
            for row in read_state()["sections"]:
                remaining = row["budget_remaining_eur"]
                total = max(start_budget.get(row["name"], remaining), remaining, 1.0)
                st.markdown(
                    f'<div class="sb-row"><div class="top"><span class="n">{esc(row["name"])}</span>'
                    f'<span class="a">€{remaining:,.2f}</span></div>{bar(remaining / total)}</div>', unsafe_allow_html=True)
        except Exception as exc:  # database not created yet
            st.caption(f"No data yet ({exc}). Use Reset demo data.")
        st.caption("Remaining vs initial budget (seeds/base.json).")
        st.write("")
        st.button("Reset demo data", on_click=reset_demo, disabled=bool(st.session_state.get("paused")), type="tertiary",
                  help="Same as: uv run python -m backend.reset seeds/base.json. Disabled while a run waits for a human.")


def render_approval(payload: dict) -> None:
    amount = payload.get("amount_eur")
    with st.container(border=True, key="approval"):
        st.markdown('<div class="hero-title">Approval required</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="amount">{f"€{amount:,.2f}" if amount is not None else "No amount"}</div>'
            f'<div class="meta"><div><div class="k">Beneficiary</div><div class="v">{esc(payload.get("beneficiary") or "—")}</div></div>'
            f'<div><div class="k">Section</div><div class="v">{esc(payload.get("section") or "—")}</div></div></div>'
            f'<div class="action">{esc(payload.get("action") or payload.get("tool"))}</div>'
            + (f'<div class="small"><b>Rule:</b> {esc(payload["rule"])}</div>' if payload.get("rule") else "")
            + (f'<div class="small"><b>Irreversible:</b> {esc(payload["irreversible"])}</div>' if payload.get("irreversible") else "")
            + (f'<div class="alert">⚠️ {esc(str(payload["warning"]).lstrip("⚠️ \ufe0f"))}</div>' if payload.get("warning") else ""),
            unsafe_allow_html=True)
        with st.expander("Tool call"):
            st.json({"tool": payload.get("tool"), "args": payload.get("args")})
        st.text_input("Reason (used if you reject)", key="reject_reason")
        ok, no = st.columns(2)
        ok.button("Approve", type="primary", use_container_width=True, on_click=resume,
                  args=({"approve": True, "reason": "approved in the UI"},))
        no.button("Reject", use_container_width=True, on_click=resume, args=(None,), kwargs={"reject": True})


def render_question(payload: dict) -> None:
    with st.container(border=True, key="question"):
        st.markdown('<div class="hero-title">The agent needs your answer</div>'
                    f'<div class="question">{esc(payload.get("question", ""))}</div>', unsafe_allow_html=True)
        st.text_input("Your answer", key="answer_text")
        st.button("Send answer", type="primary", on_click=resume, kwargs={"answer": True})


def render_final(values: dict) -> None:
    used = len(values.get("tool_calls", []))
    tiles = [("Steps", f"{used} / {st.session_state.get('max_used', DEFAULT_MAX_STEPS)}"),
             ("Tokens in / out", f"{values.get('tokens_in', 0):,} / {values.get('tokens_out', 0):,}"),
             ("Cost", f"${values.get('cost_usd', 0.0):.5f}"),
             ("Latency", f"{st.session_state.get('elapsed', 0.0):.1f} s")]
    answer = values.get("answer")
    with st.container(border=True, key="final"):
        st.markdown(
            '<div class="hero-title" style="color:#13213A">Result</div>'
            f'<div class="answer">{esc(answer) if answer else "<i>No answer (the run stopped before a final answer).</i>"}</div>'
            .replace("\n", "<br>")
            + '<div class="tiles">' + "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{esc(v)}</div></div>'
                                              for k, v in tiles) + "</div>"
            f'<div style="margin-top:12px"><span class="stopbadge">stop: {esc(values.get("stop_reason") or "—")}</span>'
            '<span class="small" style="margin-left:10px">Latency excludes the time spent waiting for a human.</span></div>',
            unsafe_allow_html=True)


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
st.set_page_config(page_title="BDS Treasury Agent", page_icon="💶", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
render_header()
st.session_state.setdefault("task", "")
st.session_state.setdefault("max_steps", DEFAULT_MAX_STEPS)
sidebar_box = st.sidebar.empty()
if st.session_state.pop("notice", None):
    st.toast("Demo data reset.")

with st.container(key="taskrow"):
    c_task, c_steps, c_run = st.columns([6, 1.3, 1], vertical_alignment="bottom")
    c_task.text_area("Task", key="task", height=80, placeholder="Type a task for the treasury agent…")
    c_steps.number_input("Max steps", min_value=1, max_value=30, step=1, key="max_steps", help="Budget of tool calls")
    c_run.button("Run", type="primary", on_click=start, use_container_width=True)
with st.container(key="chips"):
    for row in (range(0, 3), range(3, 6)):
        cols = st.columns(3)
        for col, i in zip(cols, row):
            col.button(CHIPS[i], key=f"ex{i + 1}", on_click=prefill, args=(EXAMPLES[i],), help=EXAMPLES[i],
                       use_container_width=True)
st.caption("Examples only fill the box; run them in order after one reset.")
st.write("")

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
