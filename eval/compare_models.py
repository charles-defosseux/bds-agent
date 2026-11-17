"""Bonus 1: two models, same prompt and code, same 30 x 3 scenarios -> one comparison table (Markdown, French).

    uv run python eval/compare_models.py eval/results.jsonl eval/runs/v4_model2/results.jsonl --out eval/runs/v4_model2/compare_models.md

Reuses eval/report.py (Wilson, pass^k, percentiles, adversarial numbers) and eval/compare.py (paired sign test).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import compare  # noqa: E402
from eval.report import KINDS, adversarial_numbers, fmt_rate, group_by_scenario, load, percentile, rates  # noqa: E402


def column(runs: list[dict]) -> dict:
    sc = group_by_scenario(runs)
    k = max(len(rs) for rs in sc.values())
    pass_runs = sum(r["outcome"] == "pass" for r in runs)
    adv = adversarial_numbers(runs)
    cost, lat = [r["cost_usd"] for r in runs], [r["latency_s"] for r in runs]
    by_kind = {kind: rates({s: rs for s, rs in sc.items() if rs[0]["kind"] == kind}, k) for kind in KINDS}
    return {"k": k, "sc": sc, "model": runs[0]["model"], "prompt": runs[0]["prompt_version"], "runs": len(runs),
            "pass_runs": pass_runs, "by_kind": by_kind, "total": rates(sc, k), "adv": adv, "cost": cost, "lat": lat,
            "steps": [r["steps"] for r in runs], "calls": [len(r["tool_calls"]) for r in runs],
            "tin": sum(r["tokens_in"] for r in runs), "tout": sum(r["tokens_out"] for r in runs),
            "errors": sum(bool(r["error"]) for r in runs)}


def render(a_runs: list[dict], b_runs: list[dict]) -> str:
    a, b = column(a_runs), column(b_runs)
    k = a["k"]
    rows = [("Modèle", a["model"], b["model"]), ("Prompt", a["prompt"], b["prompt"])]
    for kind in KINDS:
        rows.append((f"Succès {kind} : pass^{k} (n = {a['by_kind'][kind]['n']})",
                     *[fmt_rate(x["by_kind"][kind]["all_k"], x["by_kind"][kind]["n"]) for x in (a, b)]))
    rows += [(f"**pass^{k} total** (30 scénarios)", *[fmt_rate(x["total"]["all_k"], x["total"]["n"]) for x in (a, b)]),
             (f"pass@{k} total", *[fmt_rate(x["total"]["at_k"], x["total"]["n"]) for x in (a, b)]),
             ("Succès par run", *[fmt_rate(x["pass_runs"], x["runs"]) for x in (a, b)]),
             ("Runs en erreur (API, timeout)", a["errors"], b["errors"]),
             ("Injections suivies (adversarial, runs)", *[f"{len(x['adv']['followed'])}/{x['adv']['runs']}" for x in (a, b)]),
             ("Injections arrivées au backend", *[f"{len(x['adv']['reached'])}/{x['adv']['runs']}" for x in (a, b)]),
             ("Étapes (appels au modèle) médiane / moyenne", *[f"{percentile(x['steps'], 50):.0f} / {sum(x['steps']) / x['runs']:.2f}" for x in (a, b)]),
             ("Appels d'outils moyens par run", *[f"{sum(x['calls']) / x['runs']:.2f}" for x in (a, b)]),
             ("Tokens entrée / sortie (total)", *[f"{x['tin']:,} / {x['tout']:,}".replace(",", " ") for x in (a, b)]),
             ("Coût total", *[f"{sum(x['cost']):.4f} $" for x in (a, b)]),
             ("Coût par succès", *[f"{sum(x['cost']) / x['pass_runs']:.5f} $" if x["pass_runs"] else "n/a" for x in (a, b)]),
             ("Latence p50 / p95 par run", *[f"{percentile(x['lat'], 50):.1f} s / {percentile(x['lat'], 95):.1f} s" for x in (a, b)])]
    out = ["# Comparaison de deux modèles (même prompt, même code)", "",
           f"| Mesure | A — {a['model']} | B — {b['model']} |", "|---|---|---|"]
    out += [f"| {m} | {x} | {y} |" for m, x, y in rows]
    c = compare(a_runs, b_runs)
    out += ["", f"Comparaison appariée par scénario (A → B) : {c['improved']} scénario(s) meilleur(s) avec B, {c['worsened']} moins bon(s), "
            f"{c['ties']} égalité(s) ; test des signes exact bilatéral p = {c['p_value']:.3f}.",
            "", "Intervalles de Wilson à 95 % avec n = scénarios. Étapes = appels au modèle ; coût = `usage.cost` d'OpenRouter.",
            "", "## Scénarios où les modèles diffèrent", "", "| Scénario | Type | A | B |", "|---|---|---|---|"]
    out += [f"| {r['id']} | {r['kind']} | {r['before']} | {r['after']} |" for r in c["rows"] if r["delta"]]
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a"), p.add_argument("b"), p.add_argument("--out", required=True)
    args = p.parse_args()
    text = render(load(Path(args.a)), load(Path(args.b)))
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
