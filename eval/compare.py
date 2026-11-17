"""Paired before/after comparison (phase 7): same scenarios, same model, two prompt/tool versions.

    uv run python eval/compare.py eval/runs/v1/results.jsonl eval/runs/v2/results.jsonl
    uv run python eval/compare.py eval/runs/v1/heldout/results.jsonl eval/runs/v2/heldout/results.jsonl --out x.md

Unit = the SCENARIO (its k runs are not independent). A scenario improves if it passes more of its k runs after than
before, worsens if fewer, ties otherwise. Sign test: exact two-sided binomial test on improved vs worsened, ties dropped.
"""
import argparse
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.report import group_by_scenario, load  # noqa: E402


def sign_test(improved: int, worsened: int) -> float:
    """Exact two-sided p-value of the sign test (ties already removed). 1.0 when there is nothing to test."""
    n = improved + worsened
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(0, min(improved, worsened) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def compare(before: list[dict], after: list[dict]) -> dict:
    b, a = group_by_scenario(before), group_by_scenario(after)
    rows = []
    for sid in sorted(set(b) & set(a)):
        wins_b = sum(r["outcome"] == "pass" for r in b[sid])
        wins_a = sum(r["outcome"] == "pass" for r in a[sid])
        rows.append({"id": sid, "kind": b[sid][0]["kind"], "before": f"{wins_b}/{len(b[sid])}",
                     "after": f"{wins_a}/{len(a[sid])}", "delta": wins_a - wins_b,
                     "all_pass": wins_b == len(b[sid]) and wins_a == len(a[sid])})
    improved = sum(r["delta"] > 0 for r in rows)
    worsened = sum(r["delta"] < 0 for r in rows)
    return {"rows": rows, "improved": improved, "worsened": worsened, "ties": len(rows) - improved - worsened,
            "p_value": sign_test(improved, worsened),
            "runs_before": sum(r["outcome"] == "pass" for r in before), "n_before": len(before),
            "runs_after": sum(r["outcome"] == "pass" for r in after), "n_after": len(after)}


def render(c: dict, title: str) -> str:
    lines = [f"## {title}", "",
             f"- Runs réussis : {c['runs_before']}/{c['n_before']} avant → {c['runs_after']}/{c['n_after']} après",
             f"- Scénarios : {c['improved']} améliorés, {c['worsened']} dégradés, {c['ties']} inchangés · "
             f"sign test exact bilatéral : p = {c['p_value']:.3f}", "",
             "Scénarios qui ne passent pas k/k avant et après (les autres : inchangés à k/k) :", "",
             "| Scénario | Type | Avant | Après | Δ |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['kind']} | {r['before']} | {r['after']} | {r['delta']:+d} |"
              for r in c["rows"] if not r["all_pass"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--title", default="Comparaison appariée avant / après")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    text = render(compare(load(Path(a.before)), load(Path(a.after))), a.title)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
