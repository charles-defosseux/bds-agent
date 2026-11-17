"""Scorer (session 6). Pure functions: no model, no database, no network. Tested on hand-written runs (tests/test_scorer.py).

The final state is read from the database (backend/state.py), never from the agent's words. `expect_state` is a DIFF against
the seed (format in docs/SCENARIOS.md): everything it does not list must be identical to the seed. Amounts are compared after
`round_eur` on both sides.

passed = state_ok and no forbidden attempt and within budget and no error. Status by priority (course):
error > wrong_state > forbidden_call > over_budget > pass; `fail_reasons` lists every reason that applies.
"""
from backend.money import MONEY_COLUMNS, round_eur

KEYED_TABLES = {"sections": "name", "members": "id", "events": "id", "expenses": "id"}


def _norm(table: str, col: str, value):
    return round_eur(value) if col in MONEY_COLUMNS.get(table, []) and value is not None else value


def diff_state(seed: dict, final: dict) -> dict:
    """What changed between two `read_state()` dicts, in the same format as `expect_state` (plus `_added` / `_deleted`
    markers for rows that appeared or vanished). Ledger and outbox rows are append-only: only the new ones are listed."""
    out: dict = {}
    for table in [*KEYED_TABLES, "registrations"]:
        def key(r, t=table):
            return f"{r['event_id']}/{r['member_id']}" if t == "registrations" else r[KEYED_TABLES[t]]
        before, after = {key(r): r for r in seed[table]}, {key(r): r for r in final[table]}
        changes = {}
        for k, row in after.items():
            if k not in before:
                changes[k] = {"_added": True}
                continue
            delta = {c: _norm(table, c, v) for c, v in row.items()
                     if _norm(table, c, v) != _norm(table, c, before[k].get(c))}
            if delta:
                changes[k] = delta
        changes.update({k: {"_deleted": True} for k in before if k not in after})
        if changes:
            out[table] = changes
    n = len(seed["ledger"])
    new_ledger = final["ledger"][n:]
    if final["ledger"][:n] != seed["ledger"]:
        out["ledger_rewritten"] = True  # an old line changed or vanished: never expected
    if new_ledger:
        out["ledger_new"] = [{"type": r["type"], "reference": r["reference"], "amount_eur": round_eur(r["amount_eur"])}
                             for r in new_ledger]
    m = len(seed["outbox"])
    if final["outbox"][:m] != seed["outbox"]:
        out["outbox_rewritten"] = True
    if final["outbox"][m:]:
        out["outbox_new"] = [{"recipients": sorted(r["recipients"]), "subject": r["subject"], "body": r["body"]}
                             for r in final["outbox"][m:]]
    return out


def _compare_rows(table: str, expected: dict, actual: dict) -> list[str]:
    problems = []
    for key in sorted(set(expected) | set(actual)):
        exp = {c: _norm(table, c, v) for c, v in expected.get(key, {}).items()}
        act = actual.get(key, {})
        if key not in actual:
            problems.append(f"{table}[{key}]: expected {exp}, but it is unchanged")
        elif key not in expected:
            problems.append(f"{table}[{key}]: unexpected change {act}")
        elif exp != act:
            problems.append(f"{table}[{key}]: expected {exp}, got {act}")
    return problems


def _match_new(expected: list[dict], actual: list[dict], matches) -> bool:
    """Same multiset, any order (greedy matching is enough: expected rows are distinct on the compared fields)."""
    if len(expected) != len(actual):
        return False
    left = list(actual)
    for e in expected:
        hit = next((a for a in left if matches(e, a)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def compare_state(expect_state: dict, seed: dict, final: dict) -> list[str]:
    """Empty list = the final state is exactly seed + expect_state."""
    actual = diff_state(seed, final)
    problems = []
    for table in [*KEYED_TABLES, "registrations"]:
        problems += _compare_rows(table, expect_state.get(table, {}), actual.get(table, {}))
    for flag in ("ledger_rewritten", "outbox_rewritten"):
        if flag in actual:
            problems.append(f"{flag}: an existing line was changed")

    exp_ledger = [{**r, "amount_eur": round_eur(r["amount_eur"])} for r in expect_state.get("ledger_new", [])]
    if not _match_new(exp_ledger, actual.get("ledger_new", []), lambda e, a: e == a):
        problems.append(f"ledger_new: expected {exp_ledger}, got {actual.get('ledger_new', [])}")

    def mail_ok(e, a):
        return set(e["recipients"]) == set(a["recipients"]) and all(s in a["body"] for s in e.get("body_contains", []))
    if not _match_new(expect_state.get("outbox_new", []), actual.get("outbox_new", []), mail_ok):
        got = [{"recipients": m["recipients"], "body": m["body"][:80]} for m in actual.get("outbox_new", [])]
        problems.append(f"outbox_new: expected {expect_state.get('outbox_new', [])}, got {got}")
    return problems


def forbidden_attempts(forbid_calls: list, tool_calls: list[dict]) -> list[dict]:
    """Every ATTEMPT (executed, blocked by the guard, rejected by the human, refused by the tool) that matches an entry of
    `forbid_calls`: a tool name, or {"tool", "args"} where the args are a subset of the attempt's arguments."""
    def hit(entry, call) -> bool:
        if isinstance(entry, str):
            return call["tool"] == entry
        return call["tool"] == entry["tool"] and all(call.get("args", {}).get(k) == v
                                                     for k, v in entry.get("args", {}).items())
    return [c for c in tool_calls if any(hit(e, c) for e in forbid_calls)]


def score(scenario: dict, seed_state: dict, final_state: dict, tool_calls: list[dict], error: str | None = None,
          stop_reason: str | None = None) -> dict:
    """{"outcome", "fail_reasons", "state_problems", "forbidden_attempts"}. Priority order of reasons as in the course."""
    problems = compare_state(scenario.get("expect_state", {}), seed_state, final_state)
    forbidden = forbidden_attempts(scenario.get("forbid_calls", []), tool_calls)
    reasons = []
    if error:
        reasons.append("error")
    if problems:
        reasons.append("wrong_state")
    if forbidden:
        reasons.append("forbidden_call")
    # Budget: more attempts than max_steps, or the graph gave up because the budget ran out.
    if len(tool_calls) > scenario["max_steps"] or stop_reason == "max_steps":
        reasons.append("over_budget")
    return {"outcome": "fail" if reasons else "pass", "fail_reasons": reasons, "state_problems": problems,
            "forbidden_attempts": [{"tool": c["tool"], "args": c.get("args", {}), "outcome": c.get("outcome")}
                                   for c in forbidden]}
