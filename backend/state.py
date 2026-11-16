"""read_state(): the full backend state as a JSON-serialisable dict. The scorer reads this, never the agent's words."""
import json
from pathlib import Path

from backend.db import connect

_ORDER = {
    "sections": "name",
    "members": "id",
    "events": "id",
    "registrations": "event_id, member_id",
    "expenses": "id",
    "ledger": "id",
    "outbox": "id",
}
_BOOL_COLUMNS = {
    "members": ["dues_paid"],
    "expenses": ["has_receipt", "president_approved", "vendor_blocked"],
}


def read_state(path: str | Path | None = None) -> dict:
    conn = connect(path)
    try:
        state: dict[str, list[dict]] = {}
        for table, order in _ORDER.items():
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            for row in rows:
                for col in _BOOL_COLUMNS.get(table, []):
                    row[col] = bool(row[col])
                if table == "outbox":
                    row["recipients"] = json.loads(row["recipients"])
            state[table] = rows
        return state
    finally:
        conn.close()
