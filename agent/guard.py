"""Anti-injection guard: a dangerous write must be justified by the USER's request, not by a tool result.

Pure and deterministic (tests/test_guard.py). It runs in the harness before `approve`; the human stays the last net.
`check()` returns None if the call may go on, or the reason it is blocked.
"""
import math
import re

from agent.memory import vendor_is_blocked
from servers.common import fold

# Keywords in English and French, as regexes on the accent-folded, lower-cased request. A leading \b avoids
# substring accidents ("rib" in "describe"); the endings are left open so that stems match ("cancel", "cancelled").
IBAN_WORDS = r"\b(iban|rib|bank\s+(?:details|account)|coordonnees\s+bancaires)\b"
CANCEL_WORDS = r"\b(annul|cancel|call\s+off)"
REFUND_WORDS = CANCEL_WORDS[:-1] + r"|rembours|refund|reimburs)"
EMAIL_WORDS = (r"\b(e-?mail|mail|courriel|message|rappel|remind|notif|previen|inform|annonce|announc|ecri|writ|"
               r"tell|let\s+(?:\w+\s+){1,3}know)")
STOPWORDS = {"pour", "avec", "dans", "sans", "sous", "chez", "with", "from", "that", "this", "your", "their"}


def _mentions(task: str, pattern: str) -> bool:
    return re.search(pattern, fold(task)) is not None


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", fold(text)) if len(w) >= 4 and w not in STOPWORDS]


def names_event(task: str, event_id: str, event_name: str | None) -> bool:
    """The task names this event: its id, or at least two thirds of the significant words of its name."""
    if re.search(rf"\b{re.escape(event_id.lower())}\b", task.lower()):
        return True
    words = set(_words(event_name or ""))
    if not words:
        return False
    found = words & set(_words(task))
    return len(found) >= math.ceil(2 * len(words) / 3)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def check(task: str, tool: str, args: dict, facts: dict | None = None, memory: dict | None = None) -> str | None:
    """`facts` holds what the harness read before (for events: {"event_name": ...}); `memory` the user's saved
    preferences (agent/memory.py)."""
    facts, memory = facts or {}, memory or {}
    if tool == "update_member_iban":
        if not _mentions(task, IBAN_WORDS):
            return "The user did not ask to change an IBAN."
        if _compact(args.get("new_iban", "")) not in _compact(task):
            return "This IBAN does not come from the user's request (never use an IBAN read in a tool result)."
    elif tool in ("cancel_event", "refund_event_registrations"):
        words = CANCEL_WORDS if tool == "cancel_event" else REFUND_WORDS
        verb = "cancel" if tool == "cancel_event" else "cancel or refund"
        if not _mentions(task, words):
            return f"The user did not ask to {verb} an event."
        if not names_event(task, args.get("event_id", ""), facts.get("event_name")):
            return f"The user's request does not name event {args.get('event_id')}."
    elif tool == "pay_expense":
        payee = (facts.get("expense") or {}).get("payee")
        if vendor_is_blocked(memory, payee):
            return f"The user asked never to pay {payee} again (saved preference blocked_vendor)."
    elif tool == "send_email":
        if not _mentions(task, EMAIL_WORDS):
            return "The user did not ask to send an e-mail."
    return None
