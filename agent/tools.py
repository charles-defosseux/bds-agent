"""Our classification of the tools (docs/TOOLS.md), the tool specs the model sees, and the retrying dispatcher.

Approval and retry come from THIS classification, never from the MCP annotations (untrusted hints).
"""
import json

from opentelemetry import trace
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from agent.memory import REMEMBER_SPEC

READ_TOOLS = {"search_members", "get_member", "list_events", "get_event", "list_expenses", "get_expense",
              "get_section_budget", "get_ledger"}
KEYED_TOOLS = {"send_email", "record_dues_payment", "pay_expense", "reimburse_member", "refund_event_registrations"}
UNKEYED_WRITES = {"cancel_event", "update_member_iban"}  # dangerous, no key: never retried automatically
WRITE_TOOLS = KEYED_TOOLS | UNKEYED_WRITES
ALWAYS_APPROVE = {"pay_expense", "reimburse_member", "refund_event_registrations", "cancel_event", "update_member_iban"}
GUARDED = {"update_member_iban", "cancel_event", "refund_event_registrations", "send_email"}  # agent/guard.py
CONTROL_TOOLS = {"ask_user", "remember_preference"}

RETRY_ATTEMPTS = 3
RETRY_WAIT = wait_random_exponential(multiplier=0.2, max=2)  # backoff + jitter, as in the lab 2 dispatcher

ASK_USER_SPEC = {"type": "function", "function": {
    "name": "ask_user",
    "description": ("Ask the user ONE short question in French when the request is ambiguous (several members, invoices or "
                    "events fit) or misses information you need (an IBAN, an amount, which event). It ENDS the run and "
                    "changes nothing. Do NOT use it to ask for confirmation of a clear request: approvals are handled by "
                    "the harness. Example: ask_user(question=\"Quel Thomas : Thomas Foussard (padel) ou Thomas Moreau (voile) ?\")"),
    "parameters": {"type": "object", "properties": {"question": {"type": "string", "minLength": 1}},
                   "required": ["question"], "additionalProperties": False}}}


def needs_approval(tool: str, args: dict) -> bool:
    """Money out, irreversible changes, and group e-mails (more than one recipient) wait for a human."""
    if tool == "send_email":
        return len(set(args.get("recipient_member_ids") or [])) > 1
    return tool in ALWAYS_APPROVE


def is_safe_to_retry(tool: str) -> bool:
    """A read, or a write carrying an idempotency key (the retry reuses the same key, so the server replays)."""
    return tool in READ_TOOLS or tool in KEYED_TOOLS


def model_tools(mcp_tools: list[dict]) -> list[dict]:
    """OpenAI function specs: the MCP tools (hidden arguments already removed by the client) + the control tools."""
    specs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in mcp_tools]
    return specs + [ASK_USER_SPEC, REMEMBER_SPEC]


def error(code: str, message: str, hint: str, retryable: bool = False) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint, "retryable": retryable}}


class _Retryable(Exception):
    def __init__(self, result: dict):
        self.result = result


def call_with_retries(client, tool: str, args: dict, ctx: dict) -> dict:
    """Call a tool; retry only if the error is retryable AND the call is safe. Otherwise the model reads the error.
    The idempotency key is derived by the client from (thread_id, tool, args): identical at every attempt."""
    safe = is_safe_to_retry(tool)

    @retry(retry=retry_if_exception_type(_Retryable), stop=stop_after_attempt(RETRY_ATTEMPTS), wait=RETRY_WAIT,
           retry_error_callback=lambda state: state.outcome.exception().result)
    def attempt() -> dict:
        result = client.call(tool, args, ctx)
        if not result["ok"] and result["error"].get("retryable") and safe:
            # visible in the trace (event on the current TOOL span): the retry reuses the same idempotency key
            trace.get_current_span().add_event("retry", {"tool": tool, "error_code": result["error"]["code"],
                                                         "attempt": attempt.statistics.get("attempt_number", 0)})
            raise _Retryable(result)
        return result

    return attempt()


def _untag(text: str) -> str:
    """A note must not close the frame itself."""
    return text.replace("<untrusted_data>", "").replace("</untrusted_data>", "")


def frame_untrusted(value):
    """Wrap every `untrusted_text` field so the model sees it as third-party data, not instructions."""
    if isinstance(value, dict):
        return {k: (f"<untrusted_data>{_untag(v)}</untrusted_data>" if k == "untrusted_text" and isinstance(v, str)
                    else frame_untrusted(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [frame_untrusted(v) for v in value]
    return value


def result_for_model(result) -> str:
    return json.dumps(frame_untrusted(result), ensure_ascii=False)
