"""reset(seed_path): rebuild the database from scratch so that every run starts from the same state."""
import json
import sys
from pathlib import Path

from backend.db import SCHEMA_PATH, connect, db_path
from backend.iban import is_valid_iban
from backend.money import MONEY_COLUMNS, round_eur

# Insertion order matters (foreign keys). Column names come from the seed, validated by the schema.
TABLES = ["sections", "members", "events", "registrations", "expenses", "ledger", "outbox"]
IBAN_COLUMNS = {"members": "iban", "expenses": "vendor_iban"}


def _check_ibans(seed: dict) -> None:
    for table, column in IBAN_COLUMNS.items():
        for row in seed.get(table, []):
            iban = row.get(column)
            if iban is not None and not is_valid_iban(iban):
                raise ValueError(f"invalid IBAN checksum in seed ({table}.{column}): {iban}")


def reset(seed_path: str | Path, path: str | Path | None = None) -> Path:
    """Delete the database file, recreate the schema and load the seed. Returns the database path."""
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    unknown = set(seed) - set(TABLES)
    if unknown:
        raise ValueError(f"unknown tables in seed: {sorted(unknown)}")
    _check_ibans(seed)

    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    conn = connect(target)
    try:
        with conn:  # one transaction: a bad seed leaves no half-built state behind
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            for table in TABLES:
                for row in seed.get(table, []):
                    row = dict(row)
                    for col in MONEY_COLUMNS.get(table, []):
                        if col in row:
                            row[col] = round_eur(row[col])
                    cols = list(row)
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [json.dumps(row[c]) if isinstance(row[c], list) else row[c] for c in cols],
                    )
    except Exception:
        conn.close()
        target.unlink(missing_ok=True)
        raise
    conn.close()
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: uv run python -m backend.reset seeds/base.json")
    print(f"database reset: {reset(sys.argv[1])}")
