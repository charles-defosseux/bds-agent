import json
import sqlite3
from pathlib import Path

import pytest

from backend.db import connect
from backend.iban import is_valid_iban
from backend.reset import reset
from backend.state import read_state

SEED = Path(__file__).parent / "fixtures" / "mini_seed.json"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    reset(SEED, path)
    return path


def test_reset_loads_seed(db):
    state = read_state(db)
    assert [s["name"] for s in state["sections"]] == ["football", "padel", "tennis", "voile"]
    assert len(state["members"]) == 2 and len(state["expenses"]) == 1
    assert state["members"][0]["dues_paid"] is True and state["members"][1]["dues_paid"] is False
    assert state["expenses"][0]["vendor_blocked"] is False
    assert state["outbox"] == []


def test_state_is_json_serialisable(db):
    json.dumps(read_state(db))


def test_reset_restores_identical_state(db):
    before = read_state(db)
    conn = connect(db)
    with conn:
        conn.execute("UPDATE sections SET budget_remaining_eur = 0 WHERE name = 'voile'")
        conn.execute("INSERT INTO ledger (type, amount_eur, reference, idempotency_key, actor_id) "
                     "VALUES ('dues', 5, 'M002', 'k', 'M001')")
    conn.close()
    assert read_state(db) != before
    reset(SEED, db)
    after = read_state(db)
    # created_at is set by the database clock: compare everything else
    for state in (before, after):
        for row in state["ledger"]:
            row.pop("created_at")
    assert after == before


def test_reset_is_seen_by_a_new_connection(db):
    """Servers open one connection per call, so a reset must be visible without restarting them."""
    conn = connect(db)
    with conn:
        conn.execute("UPDATE sections SET budget_remaining_eur = 1 WHERE name = 'football'")
    conn.close()
    reset(SEED, db)
    conn = connect(db)
    assert conn.execute("SELECT budget_remaining_eur FROM sections WHERE name = 'football'").fetchone()[0] == 1000
    conn.close()


def test_idempotency_key_is_unique(db):
    conn = connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ledger (type, amount_eur, reference, idempotency_key, actor_id) "
                     "VALUES ('dues', 30, 'M001', 'seed-1', 'M001')")
    conn.close()


@pytest.mark.parametrize("sql", [
    "INSERT INTO sections VALUES ('rugby', 10)",
    "UPDATE sections SET budget_remaining_eur = -1 WHERE name = 'voile'",
    "UPDATE expenses SET status = 'done'",
    "UPDATE members SET section = 'rugby' WHERE id = 'M001'",
    "INSERT INTO ledger (type, amount_eur, reference, idempotency_key, actor_id) VALUES ('dues', -5, 'x', 'k2', 'a')",
])
def test_schema_rejects_bad_rows(db, sql):
    conn = connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    conn.close()


def test_invalid_iban_in_seed_is_rejected(tmp_path):
    seed = json.loads(SEED.read_text())
    seed["members"][0]["iban"] = "FR7630004000010000123456780"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(seed))
    target = tmp_path / "x.db"
    with pytest.raises(ValueError, match="IBAN"):
        reset(bad, target)
    assert not target.exists()


def test_bad_seed_leaves_no_database(tmp_path):
    seed = json.loads(SEED.read_text())
    seed["registrations"][0]["member_id"] = "M999"  # foreign key violation
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(seed))
    target = tmp_path / "x.db"
    with pytest.raises(sqlite3.IntegrityError):
        reset(bad, target)
    assert not target.exists()


def test_iban_checksum():
    assert is_valid_iban("FR76 3000 4000 0100 0012 3456 789")
    assert not is_valid_iban("FR7630004000010000123456780")
    assert not is_valid_iban("not an iban")


def test_round_eur():
    from backend.money import round_eur
    assert round_eur(12.345) == 12.35  # half up, not binary float artefact
    assert round_eur(0.1 + 0.2) == 0.3
    assert round_eur(10) == 10.0 and round_eur("7.999") == 8.0


def test_reset_rounds_seed_amounts(tmp_path):
    seed = json.loads(SEED.read_text())
    seed["sections"][0]["budget_remaining_eur"] = 100.005
    seed["expenses"][0]["amount_eur"] = 0.1 + 0.2
    seed["ledger"][0]["amount_eur"] = 12.345
    p = tmp_path / "s.json"
    p.write_text(json.dumps(seed))
    state = read_state(reset(p, tmp_path / "r.db"))
    assert state["sections"][0]["budget_remaining_eur"] == 100.01
    assert state["expenses"][0]["amount_eur"] == 0.3
    assert state["ledger"][0]["amount_eur"] == 12.35
