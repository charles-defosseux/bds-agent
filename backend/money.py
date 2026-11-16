"""Single place where euro amounts are rounded. Every write of a money column goes through round_eur()."""
from decimal import ROUND_HALF_UP, Decimal

# Columns holding euro amounts, per table (used by reset; tools in phase 3 call round_eur directly).
MONEY_COLUMNS = {
    "sections": ["budget_remaining_eur"],
    "events": ["price_eur"],
    "registrations": ["amount_paid_eur"],
    "expenses": ["amount_eur"],
    "ledger": ["amount_eur"],
}


def round_eur(amount: float | int | str) -> float:
    """Round to 2 decimals, half up (12.345 -> 12.35), going through str to avoid binary float artefacts."""
    return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
