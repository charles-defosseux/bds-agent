"""SQLite access. Every call opens its own connection, so a reset is seen at once by running servers."""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH = ROOT / "data" / "bds.db"


def db_path() -> Path:
    """Database location: BDS_DB_PATH if set, else data/bds.db."""
    return Path(os.environ.get("BDS_DB_PATH") or DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
