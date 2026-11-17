"""The agent: one ReAct loop in a LangGraph StateGraph (session 3).

    init -> recall -> think -> router -+-> read_tool -> think
                             +-> guard -+-> approve -+-> write_tool -> think     (dangerous writes, group e-mails)
                             |          |            +-> think                   (rejected by the human)
                             |          +-> write_tool -> think                  (single e-mail that passed the guard)
                             |          +-> think                                (blocked by the guard)
                             +-> write_tool -> think                             (record_dues_payment: keyed, no approval)
                             +-> think                                           (the model sent unreadable arguments)
                             +-> remember -> think                              (remember_preference, user's words only)
                             +-> ask_user -> think                                (interactive: interrupt() with the question, the answer
             |            -> END                                    comes back as the tool result; evaluation: END,
             |                                                      stop_reason = asked_user)
                             +-> give_up -> END                                  (stop_reason = max_steps)
                             +-> END                                             (final answer)

Budget: `max_steps` = number of tool-call attempts (the lab 6 scorer counts the same); model calls are bounded by the
same value (+1 for the final answer). `recursion_limit` in the config is the safety net (agent/config.py::run_config).
The history is rebuilt from `observations` at every model call: the checkpoint is all the memory a run has.
"""
import atexit
import json
import operator
import threading
import time
from typing import Annotated, Callable, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt

from agent import guard as guard_rules
from agent import llm, memory, tracing
from agent.config import DEFAULT_MAX_STEPS, DEFAULT_TREASURER
from agent.mcp_client import McpClient
from agent.prompts import SYSTEM
from agent.tools import (GUARDED, WRITE_TOOLS, call_with_retries, error, model_tools, needs_approval,
                         result_for_model)
from backend.money import round_eur


class Input(TypedDict):
    task: str
    max_steps: NotRequired[int]


class AgentState(TypedDict):
    task: str
    max_steps: int
    steps: int                                         # model calls so far
    facts: dict                                        # the user's saved preferences (recall)
    pending: dict                                      # the model's last decision: {"type": "final"|"call", ...}
    observations: Annotated[list, operator.add]        # what the model sees: {"tool", "args", "result"}
    tool_calls: Annotated[list, operator.add]          # every attempt, in order: {"tool", "args", "outcome", ...}
    transcript: Annotated[list[str], operator.add]     # human-readable log
    answer: str | None
    status: str                                        # running | done
    stop_reason: str | None                            # final_answer | asked_user | max_steps
    tokens_in: int
    tokens_out: int
    cost_usd: float


# ---- pure helpers (tested without a model) ----------------------------------------------------------------------------
NOT_RUN_PARALLEL = error("not_run", "One tool per step: this call was NOT executed.",
                         "Call it again in a later step, alone, if you still need it.")


def _parse_args(raw: str | None) -> dict:
    """Arguments of a call that is not run, for the model's history only (unreadable => {})."""
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def guard_alerts(tool_calls: list) -> list[str]:
    """Deterministic safety net: one line per write the guard blocked in this run, whatever the model says about it."""
    seen, lines = set(), []
    for c in tool_calls:
        if c.get("outcome") == "blocked_by_guard":
            line = f"⚠️ Action blocked by the guard: {c['tool']} ({c.get('reason') or c.get('error_code') or 'no reason'})"
            if line not in seen:
                seen.add(line)
                lines.append(line)
    return lines


def with_alerts(text: str | None, tool_calls: list) -> str | None:
    """The model's text followed by the guard alerts (unchanged when nothing was blocked)."""
    alerts = guard_alerts(tool_calls)
    return "\n".join([text or "", *alerts]).strip() if alerts else text


def router(state: AgentState) -> str:
    """Course order: final answer => END; budget reached => give_up (even for a write); control tool; dangerous write
    => guard (then approve); other write => write_tool; otherwise read_tool."""
    pending = state["pending"]
    if pending["type"] == "final":
        return END
    if len(state["tool_calls"]) >= state["max_steps"] or state["steps"] > state["max_steps"]:
        return "give_up"
    if pending["type"] == "question":  # interactive safety net: a final answer that is a question, treated as ask_user
        return "ask_user"
    if pending["type"] == "invalid":
        return "think"
    tool, args = pending["tool"], pending["args"]
    if tool == "ask_user":
        return "ask_user"
    if tool == "remember_preference":
        return "remember"
    if tool in GUARDED or needs_approval(tool, args):
        return "guard"
    if tool in WRITE_TOOLS:
        return "write_tool"
    return "read_tool"  # reads, and unknown names (the client answers not_found without calling anything)


def is_unwritten_question(text: str | None, tool_calls: list) -> bool:
    """Interactive safety net: the model's final answer is a question (ends with "?") and nothing was written in this run."""
    wrote = any(c["tool"] in WRITE_TOOLS and c["outcome"] == "ok" for c in tool_calls)
    return bool(text) and text.strip().endswith("?") and not wrote


def as_decision(value) -> dict:
    """The human's answer: {"approve": bool, "reason": str}. Studio's resume box may send text (lab 3 helper)."""
    if isinstance(value, dict):
        return {"approve": bool(value.get("approve")), "reason": str(value.get("reason", ""))}
    text = str(value).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return as_decision(parsed)
        text = str(parsed)
    except ValueError:
        pass
    return {"approve": text.lower() in {"true", "yes", "y", "ok", "oui", "approve"}, "reason": text}


def as_answer(value) -> str:
    """The human's free-text answer to ask_user (Studio may send a string or a {"answer": ...} object)."""
    if isinstance(value, dict):
        value = value.get("answer", value.get("text", ""))
    return str(value).strip()


def approval_payload(tool: str, args: dict, facts: dict) -> dict:
    """What the human reads before deciding (in French, the treasurer's language)."""
    p = {"tool": tool, "args": args, "action": "", "amount_eur": None, "beneficiary": None, "section": None,
         "rule": "", "irreversible": ""}
    if tool in ("pay_expense", "reimburse_member"):
        e = facts.get("expense", {})
        kind = "Pay invoice" if tool == "pay_expense" else "Reimburse expense claim"
        p.update(action=f"{kind} {args.get('expense_id')}", amount_eur=e.get("amount_eur"),
                 beneficiary=facts.get("beneficiary") or e.get("payee"), section=e.get("section"),
                 rule="Rule 2 (human approval; above €300, the president's approval). The tool also checks "
                      "rules 1 (budget), 3 (receipt), 4 (never twice) and 5 (blocked vendor).",
                 irreversible="The money leaves the section budget; a payment cannot be undone.")
    elif tool == "refund_event_registrations":
        p.update(action=f"Refund the {facts.get('registered_count')} registrants of “{facts.get('event_name')}” "
                        f"({args.get('event_id')})",
                 amount_eur=facts.get("total_collected_eur"), beneficiary="the event's registrants",
                 section=facts.get("section"), rule="Rule 2 (human approval) and rule 1 (section budget).",
                 irreversible="The money leaves the section budget; refunds cannot be undone.")
    elif tool == "cancel_event":
        p.update(action=f"Cancel “{facts.get('event_name')}” ({args.get('event_id')}, {facts.get('date')})",
                 beneficiary=f"{facts.get('registered_count')} registrants", section=facts.get("section"),
                 rule="Cancellation requested by the user (guard); does not refund or notify anyone.",
                 irreversible="A cancelled event cannot be reinstated.")
    elif tool == "update_member_iban":
        p.update(action=f"Replace the IBAN of {facts.get('member_name')} ({args.get('member_id')})",
                 beneficiary=facts.get("member_name"), section=facts.get("section"),
                 rule="Rule 6: an IBAN is changed only on the user's explicit request.",
                 irreversible=f"The old IBAN ({facts.get('old_iban_masked')}) is overwritten; future "
                              "reimbursements will go to the new account.")
        p["args"] = {**args, "new_iban": _mask(args.get("new_iban", ""))}
    elif tool == "send_email":
        n = len(set(args.get("recipient_member_ids") or []))
        p.update(action=f"Send the e-mail “{args.get('subject')}” to {n} members", beneficiary=f"{n} members",
                 rule="Rule 2: a group e-mail goes through human approval.",
                 irreversible="A sent e-mail cannot be recalled.")
    if p["amount_eur"] is not None:
        p["amount_eur"] = round_eur(p["amount_eur"])
    return p


def _mask(iban: str) -> str:
    iban = iban.replace(" ", "")
    return f"{iban[:2]}…{iban[-4:]}" if len(iban) > 6 else iban


# ---- the MCP client of this process ------------------------------------------------------------------------------------
_client: McpClient | None = None
_client_lock = threading.Lock()


def get_client() -> McpClient:
    """Both stdio servers, started once per process (Studio, scripts, eval)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = McpClient().start()
            atexit.register(_client.close)
        return _client


def _ctx(config) -> dict:
    """Harness context: the run's thread and the treasurer. Never chosen by the model."""
    c = config.get("configurable", {})
    return {"thread_id": c["thread_id"], "user_id": c.get("user_id") or DEFAULT_TREASURER}


def _short(value, n: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= n else text[:n] + "…"


# ---- the graph -----------------------------------------------------------------------------------------------------------
def build_graph(client: McpClient | None = None, chat: Callable | None = None, checkpointer=None,
                store: BaseStore | None = None):
    """`client` and `chat` default to the real MCP servers and the real model; tests pass fakes. Studio provides its own
    checkpointer and store (compile without)."""
    chat = chat or llm.chat
    mcp = (lambda: client) if client is not None else get_client

    def init(state: Input) -> dict:
        return {"max_steps": state.get("max_steps", DEFAULT_MAX_STEPS), "steps": 0, "pending": {}, "answer": None,
                "status": "running", "stop_reason": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    def recall(state: AgentState, config, *, store: BaseStore | None = None) -> dict:
        """Load the user's exact facts; they reach the model as a KNOWN FACTS block."""
        return {"facts": memory.load(store, _ctx(config)["user_id"])}

    def think(state: AgentState, config) -> dict:
        block = memory.facts_block(state["facts"])
        msgs = [{"role": "system", "content": f"{block}\n\n{SYSTEM}" if block else SYSTEM},
                {"role": "user", "content": state["task"]}]
        for i, o in enumerate(state["observations"]):
            call = {"id": f"c{i}", "type": "function",
                    "function": {"name": o["tool"], "arguments": json.dumps(o["args"], ensure_ascii=False)}}
            msgs += [{"role": "assistant", "content": None, "tool_calls": [call]},
                     {"role": "tool", "tool_call_id": f"c{i}", "content": result_for_model(o["result"])}]
        reply = chat(msgs, model_tools(mcp().list_tools()), config.get("configurable", {}).get("model"))
        n = state["steps"] + 1
        usage = {"steps": n, "tokens_in": state["tokens_in"] + reply["tokens_in"],
                 "tokens_out": state["tokens_out"] + reply["tokens_out"],
                 "cost_usd": state["cost_usd"] + reply["cost_usd"]}
        if not reply["tool_calls"]:
            if (config.get("configurable", {}).get("interactive_questions", True)
                    and is_unwritten_question(reply["content"], state["tool_calls"])):
                question = reply["content"].strip()
                return {**usage, "pending": {"type": "question", "tool": "ask_user", "args": {"question": question}},
                        "transcript": [f"[{n}] question (final answer treated as ask_user): {question}"]}
            answer = with_alerts(reply["content"], state["tool_calls"])
            return {**usage, "pending": {"type": "final"}, "answer": answer, "status": "done",
                    "stop_reason": "final_answer", "transcript": [f"[{n}] answer: {answer}"]}
        call = reply["tool_calls"][0]  # one tool call per step, even if the model sends several
        # Fallback if the provider ignores parallel_tool_calls=False: the extra calls are NOT run and the model is told so
        # (they are not attempts: not in tool_calls, not in the budget). Shown before the call that does run.
        usage["observations"] = [{"tool": c["name"], "args": _parse_args(c["arguments"]), "result": NOT_RUN_PARALLEL}
                                 for c in reply["tool_calls"][1:]]
        try:
            args = json.loads(call["arguments"] or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            # Nothing runs; the attempt counts in the budget and the model reads the error.
            result = error("invalid_argument", f"Unreadable arguments for {call['name']}: {e}.",
                           "Send the arguments as one JSON object matching the tool schema.")
            return {**usage, "pending": {"type": "invalid", "tool": call["name"], "args": {}},
                    "observations": usage["observations"] + [{"tool": call["name"], "args": {}, "result": result}],
                    "tool_calls": [{"tool": call["name"], "args": {}, "outcome": "invalid_arguments"}],
                    "transcript": [f"[{n}] {call['name']}(<unreadable arguments>)"]}
        if call["name"] == "send_email":
            args = memory.apply_signature(args, state["facts"])
        return {**usage, "pending": {"type": "call", "tool": call["name"], "args": args},
                "transcript": [f"[{n}] {call['name']}({_short(args)})"]}

    def run_tool(state: AgentState, config) -> dict:
        tool, args = state["pending"]["tool"], state["pending"]["args"]
        t0 = time.perf_counter()
        result = tracing.traced_tool_call(tool, args, lambda: call_with_retries(mcp(), tool, args, _ctx(config)))
        attempt = {"tool": tool, "args": args, "outcome": "ok" if result["ok"] else "error",
                   "latency_s": round(time.perf_counter() - t0, 3)}
        if not result["ok"]:
            attempt["error_code"] = result["error"]["code"]
        if state["pending"].get("approved"):
            attempt["approved"] = True
        return {"observations": [{"tool": tool, "args": args, "result": result}], "tool_calls": [attempt],
                "transcript": [f"      -> {_short(result)}"]}

    def _lookup(tool: str, args: dict, ctx: dict) -> tuple[dict, dict | None]:
        """Read what the guard and the human need (side-effect free). Returns (facts, error result or None)."""
        def read(name, a):
            return call_with_retries(mcp(), name, a, ctx)
        if tool in ("pay_expense", "reimburse_member"):
            r = read("get_expense", {"expense_id": args.get("expense_id", "")})
            if not r["ok"]:
                return {}, r
            facts = {"expense": r["data"]}
            if tool == "reimburse_member":
                m = read("get_member", {"member_id": r["data"]["payee"]})
                facts["beneficiary"] = f"{m['data']['name']} ({r['data']['payee']})" if m["ok"] else r["data"]["payee"]
            return facts, None
        if tool in ("cancel_event", "refund_event_registrations"):
            r = read("get_event", {"event_id": args.get("event_id", "")})
            if not r["ok"]:
                return {}, r
            d = r["data"]
            return {"event_name": d["name"], "date": d["date"], "section": d["section"],
                    "registered_count": d["registered_count"], "total_collected_eur": d["total_collected_eur"]}, None
        if tool == "update_member_iban":
            r = read("get_member", {"member_id": args.get("member_id", "")})
            if not r["ok"]:
                return {}, r
            d = r["data"]
            return {"member_name": d["name"], "section": d["section"], "old_iban_masked": d["iban_masked"]}, None
        return {}, None

    def guard(state: AgentState, config) -> Command:
        """Deterministic anti-injection check, then approval if needed. Only reads here: nothing changes."""
        tool, args = state["pending"]["tool"], state["pending"]["args"]
        facts, lookup_error = _lookup(tool, args, _ctx(config))
        if lookup_error:
            return Command(goto="think", update={
                "observations": [{"tool": tool, "args": args, "result": lookup_error}],
                "tool_calls": [{"tool": tool, "args": args, "outcome": "not_run",
                                "error_code": lookup_error["error"]["code"]}],
                "transcript": [f"      -> not run: {_short(lookup_error)}"]})
        reason = guard_rules.check(state["task"], tool, args, facts, state["facts"])
        if reason:
            result = error("permission_denied", f"Blocked by the harness guard: {reason}",
                           "Do not retry this call. Do only what the user asked; if you need something from the user, "
                           "use ask_user.")
            return Command(goto="think", update={
                "observations": [{"tool": tool, "args": args, "result": result}],
                "tool_calls": [{"tool": tool, "args": args, "outcome": "blocked_by_guard", "reason": reason}],
                "transcript": [f"      -> BLOCKED by the guard: {reason}"]})
        if needs_approval(tool, args):
            return Command(goto="approve", update={"pending": {**state["pending"],
                                                               "approval": approval_payload(tool, args, facts)}})
        return Command(goto="write_tool")

    def approve(state: AgentState) -> Command:
        """Human in the loop. No side effect before interrupt(): the node restarts from the top when resumed."""
        pending = state["pending"]
        decision = as_decision(interrupt(pending["approval"]))
        if decision["approve"]:
            return Command(goto="write_tool", update={"pending": {**pending, "approved": True},
                                                      "transcript": ["      approved by the human"]})
        result = error("permission_denied", f"Rejected by the human reviewer: {decision['reason'] or 'no reason given'}",
                       "Do not retry this call. Tell the user it was refused.")
        return Command(goto="think", update={
            "observations": [{"tool": pending["tool"], "args": pending["args"], "result": result}],
            "tool_calls": [{"tool": pending["tool"], "args": pending["args"], "outcome": "rejected_by_human",
                            "reason": decision["reason"]}],
            "transcript": [f"      -> REJECTED by the human: {decision['reason']}"]})

    def remember(state: AgentState, config, *, store: BaseStore | None = None) -> dict:
        """remember_preference: only the user's words write (memory.check_remember); the model reads the outcome."""
        args = state["pending"]["args"]
        key, value = str(args.get("key", "")), str(args.get("value", ""))
        problem = memory.check_remember(state["task"], key, value)
        if problem is None and store is None:
            problem = ("internal", "No memory store is available.", "Tell the user the preference was not saved.")
        if problem:
            code, message, hint = problem
            result, outcome = error(code, message, hint), "blocked_by_guard" if code == "permission_denied" else "error"
            attempt = {"tool": "remember_preference", "args": args, "outcome": outcome, "error_code": code}
        else:
            memory.upsert(store, _ctx(config)["user_id"], key, value)
            result = {"ok": True, "data": {"key": key, "value": value, "saved": True}}
            attempt = {"tool": "remember_preference", "args": args, "outcome": "ok"}
        update = {"observations": [{"tool": "remember_preference", "args": args, "result": result}],
                  "tool_calls": [attempt], "transcript": [f"      -> {_short(result)}"]}
        if not problem:
            update["facts"] = {**state["facts"], key: value}
        return update

    def ask_user(state: AgentState, config) -> Command:
        """Pattern 4, "ask a question". Interactive (Studio, scripts): interrupt() with the question; the human's free-text
        answer goes back to the model as the tool result and the run continues. Evaluation (`interactive_questions`
        false): the question ends the run, as before. No side effect before interrupt(): the node restarts when resumed."""
        args = state["pending"]["args"]
        question = str(args.get("question", ""))
        attempt = {"tool": "ask_user", "args": args, "outcome": "ok"}
        if not config.get("configurable", {}).get("interactive_questions", True):
            return Command(goto=END, update={
                "answer": with_alerts(question, state["tool_calls"]), "status": "done", "stop_reason": "asked_user", "tool_calls": [attempt],
                "transcript": [f"      -> question for the user: {question}"]})
        reply = as_answer(interrupt({"question": question}))
        result = {"ok": True, "data": f"User’s answer: {reply}"}
        return Command(goto="think", update={
            "observations": [{"tool": "ask_user", "args": args, "result": result}], "tool_calls": [attempt],
            "transcript": [f"      -> question: {question}", f"      <- the human answered: {reply}"]})

    def give_up(state: AgentState) -> dict:
        return {"answer": None, "status": "done", "stop_reason": "max_steps",
                "transcript": [f"[stopped: budget of {state['max_steps']} tool calls reached]"]}

    g = StateGraph(AgentState, input_schema=Input)
    for name, fn in [("init", init), ("recall", recall), ("think", think), ("read_tool", run_tool), ("write_tool", run_tool),
                     ("remember", remember), ("give_up", give_up)]:
        g.add_node(name, fn)
    g.add_node("guard", guard, destinations=("approve", "write_tool", "think"))
    g.add_node("ask_user", ask_user, destinations=("think", END))
    g.add_node("approve", approve, destinations=("write_tool", "think"))
    g.add_edge(START, "init")
    g.add_edge("init", "recall")
    g.add_edge("recall", "think")
    g.add_conditional_edges("think", router, ["read_tool", "guard", "write_tool", "remember", "ask_user", "give_up", "think", END])
    g.add_edge("read_tool", "think")
    g.add_edge("write_tool", "think")
    g.add_edge("remember", "think")
    g.add_edge("give_up", END)
    return g.compile(checkpointer=checkpointer, store=store)
