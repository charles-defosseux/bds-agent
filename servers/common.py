"""Shared pieces of the two MCP servers: typed errors, annotations, argument types, DB helpers, financial rules.

stdio rule: never print to stdout (it carries the JSON-RPC). Logs go to stderr.
"""
import json
import unicodedata
from contextlib import contextmanager
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from backend.db import connect
from backend.money import round_eur

# ---- Financial rules (docs/REGLEMENT_FINANCIER.md) -------------------------------------------------------------------
PRESIDENT_THRESHOLD_EUR = 300.0  # above this, the expense needs president_approved = true (rule 2)

# ---- MCP annotations: hints for clients. Approval comes from OUR classification (docs/TOOLS.md), not from these. ----
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=False)
WRITE_KEYED = ToolAnnotations(readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=False)
WRITE_DANGEROUS = ToolAnnotations(readOnlyHint=False, idempotentHint=False, destructiveHint=True, openWorldHint=False)

# ---- Argument types: the schema the model sees is built from these ---------------------------------------------------
Section = Literal["football", "tennis", "padel", "voile"]
MemberId = Annotated[str, Field(pattern=r"^M\d{3}$", description="Member id, e.g. M003")]
EventId = Annotated[str, Field(pattern=r"^EV\d{2}$", description="Event id, e.g. EV01")]
ExpenseId = Annotated[str, Field(pattern=r"^E-\d{3}$", description="Expense id, e.g. E-001")]
AmountEur = Annotated[float, Field(gt=0, le=10000, description="Amount in euros (EUR), at most 2 decimals")]
# Hidden arguments: removed from the schema the model sees and injected by the harness (agent/mcp_client.py).
IdempotencyKey = Annotated[str | None, Field(description="Set by the harness; never by the model")]
ActorId = Annotated[str | None, Field(description="Treasurer id, set by the harness; never by the model")]
HIDDEN_ARGS = ("idempotency_key", "actor_id")


# ---- Typed errors -----------------------------------------------------------------------------------------------------
def tool_error(code: str, message: str, hint: str = "", retryable: bool = False) -> ToolError:
    """Typed error. `message` says what is wrong, `hint` what to do next (the model reads both), `retryable` is for the
    harness. In SDK 2.x only a ToolError reaches the model as `isError`; the client parses this JSON back."""
    assert code in {"invalid_argument", "not_found", "conflict", "permission_denied", "timeout", "rate_limited", "internal"}
    return ToolError(json.dumps({"code": code, "message": message, "hint": hint, "retryable": retryable}))


def require_harness_args(idempotency_key: str | None, actor_id: str | None) -> None:
    if not idempotency_key or not actor_id:
        raise tool_error("invalid_argument", "Missing harness context (idempotency key or treasurer id).",
                         "This is a harness bug, not something the model can fix: stop and report it.")


# ---- Database ----------------------------------------------------------------------------------------------------------
@contextmanager
def read_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx():
    """One immediate transaction: the checks and the writes of a tool call are atomic (no double spend under races)."""
    conn = connect()
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def fold(text: str) -> str:
    """Lowercase and strip accents, so 'chloe' finds 'Chloé'."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def mask_iban(iban: str | None) -> str | None:
    """Show only the country code and the last 4 characters: the model never needs a full IBAN."""
    return f"{iban[:2]}…{iban[-4:]}" if iban else None


def untrusted(text: str) -> str | None:
    """Free text written by third parties: returned in a field named `untrusted_text`, empty text as None."""
    return text or None


def blocking_reasons(e, budget_remaining_eur: float) -> list[str]:
    """Financial rules (docs/REGLEMENT_FINANCIER.md) that stop `e` from being paid, each with its rule number.

    Informational mirror of what pay_expense / reimburse_member enforce (`tests/test_registry.py` checks that both agree
    on every seed expense). `e` is an `expenses` row. Empty list = payable. A blocked vendor from the user's memory
    (rule 5, `blocked_vendor`) is not in the database and is NOT covered here: the harness guard handles it.
    """
    reasons = []
    claim = e["type"] == "member_reimbursement"
    if e["status"] == "paid":
        reasons.append("Rule 4: already paid (never paid twice).")
    elif e["status"] == "rejected":
        reasons.append("Status `rejected`: a rejected expense is never payable.")
    elif e["status"] != "validated":
        prefix = "Rule 3: status" if claim else "Status"
        reasons.append(f"{prefix} `{e['status']}`, not `validated`: only validated expenses can be paid.")
    if claim and not e["has_receipt"]:
        reasons.append("Rule 3: no receipt (a reimbursement needs one).")
    if e["amount_eur"] > PRESIDENT_THRESHOLD_EUR and not e["president_approved"]:
        reasons.append(f"Rule 2: {e['amount_eur']:.2f} EUR is above {PRESIDENT_THRESHOLD_EUR:.0f} EUR and the president "
                       "has not approved it.")
    if not claim and e["vendor_blocked"]:
        reasons.append(f"Rule 5: vendor {e['payee']} is blocked.")
    if e["status"] != "paid" and round_eur(budget_remaining_eur) < round_eur(e["amount_eur"]):
        reasons.append(f"Rule 1: insufficient section budget ({budget_remaining_eur:.2f} EUR left, "
                       f"{e['amount_eur']:.2f} EUR needed).")
    return reasons
