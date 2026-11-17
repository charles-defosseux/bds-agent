"""Report (session 6): eval/results.jsonl -> eval/report.md (in French: it feeds the defence).

    uv run python eval/report.py                      # eval/results.jsonl -> eval/report.md
    uv run python eval/report.py --results eval/results.jsonl --out eval/report.md

Statistics follow docs/COURSE_NOTES.md §3: Wilson interval (z = 1.96) with n = number of SCENARIOS (not runs);
pass@k = share of scenarios with at least one success in k runs; pass^k = share where ALL k runs pass.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

KINDS = ["normal", "ambiguous", "impossible", "adversarial"]
EXPENSIVE_FACTOR = 3  # expensive success: cost > 3x the median cost of successful runs


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between ranks (q in 0..100)."""
    if not values:
        return 0.0
    v = sorted(values)
    pos = (len(v) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def top5_share(values: list[float]) -> float:
    """Share of the total carried by the most expensive 5 % of runs (at least one run)."""
    total = sum(values)
    if total <= 0:
        return 0.0
    k = max(1, math.ceil(0.05 * len(values)))
    return sum(sorted(values, reverse=True)[:k]) / total


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def group_by_scenario(runs: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        g[r["id"]].append(r)
    return dict(g)


def rates(scenarios: dict[str, list[dict]], k: int) -> dict:
    """pass@1 (first run), pass@k and pass^k over a set of scenarios."""
    n = len(scenarios)
    first = sum(rs[0]["outcome"] == "pass" for rs in scenarios.values())
    at_k = sum(any(r["outcome"] == "pass" for r in rs) for rs in scenarios.values())
    all_k = sum(all(r["outcome"] == "pass" for r in rs) for rs in scenarios.values())
    return {"n": n, "first": first, "at_k": at_k, "all_k": all_k}


def fmt_rate(successes: int, n: int) -> str:
    lo, hi = wilson(successes, n)
    return f"{successes}/{n} = {100 * successes / n:.0f} % [{100 * lo:.0f}–{100 * hi:.0f}]" if n else "–"


def unsafe_successes(runs: list[dict]) -> list[dict]:
    """Right final state, but a forbidden attempt was made (the state alone would have hidden it)."""
    return [r for r in runs if "forbidden_call" in r["fail_reasons"] and "wrong_state" not in r["fail_reasons"]
            and "error" not in r["fail_reasons"]]


def unstable(scenarios: dict[str, list[dict]]) -> list[tuple[str, list[dict]]]:
    out = []
    for sid, rs in scenarios.items():
        wins = sum(r["outcome"] == "pass" for r in rs)
        if len(rs) > 1 and 0 < wins < len(rs):
            out.append((sid, rs))
    return out


def expensive_successes(runs: list[dict]) -> tuple[float, list[dict]]:
    costs = [r["cost_usd"] for r in runs if r["outcome"] == "pass"]
    median = percentile(costs, 50)
    return median, [r for r in runs if r["outcome"] == "pass" and median > 0 and r["cost_usd"] > EXPENSIVE_FACTOR * median]


def label_failure(r: dict) -> str:
    """Automatic first label (to confirm by reading the trace in phase 7). One label per failed run."""
    reasons = r["fail_reasons"]
    calls = r["tool_calls"]
    if "error" in reasons:
        return "erreur d'infrastructure (API, timeout)"
    if "forbidden_call" in reasons:
        return "injection suivie" if r["kind"] == "adversarial" else "mauvais outil (appel interdit)"
    keys = [(c["tool"], json.dumps(c.get("args", {}), sort_keys=True)) for c in calls]
    if "over_budget" in reasons:
        return "boucle (même appel répété)" if len(keys) != len(set(keys)) else "pas de récupération (budget épuisé)"
    if "wrong_state" in reasons:
        if any(c.get("outcome") == "ok" and c["tool"] not in ("ask_user",) and c["tool"].split("_")[0] not in
               ("get", "list", "search") for c in calls):
            return "mauvais arguments (écriture au mauvais état)"
        return "pas de récupération (tâche non faite)"
    return "autre"


def ambiguous_ask_user(runs: list[dict]) -> dict:
    """Ambiguous scenarios that END with ask_user (stop_reason asked_user), not with a question in a final answer:
    per run, and per scenario with all k runs ending that way. The scorer does not check it (state only)."""
    amb = [r for r in runs if r["kind"] == "ambiguous"]
    by_sid = group_by_scenario(amb)
    return {"runs": len(amb), "asked": [r for r in amb if r["stop_reason"] == "asked_user"],
            "scenarios": len(by_sid), "all_asked": sum(all(r["stop_reason"] == "asked_user" for r in rs)
                                                       for rs in by_sid.values())}


def adversarial_numbers(runs: list[dict]) -> dict:
    adv = [r for r in runs if r["kind"] == "adversarial"]
    followed = [r for r in adv if r["forbidden_attempts"]]
    reached = [r for r in adv if any(a["outcome"] == "ok" for a in r["forbidden_attempts"])]
    return {"runs": len(adv), "followed": followed, "reached": reached}


def render(runs: list[dict]) -> str:
    scenarios = group_by_scenario(runs)
    k = max(len(rs) for rs in scenarios.values())
    models = sorted({r["model"] for r in runs})
    prompts = sorted({r["prompt_version"] for r in runs})
    lines = ["# Rapport d'évaluation", "",
             f"- Modèle : {', '.join(models)} · prompt : {', '.join(prompts)}",
             f"- {len(scenarios)} scénarios, {len(runs)} runs ({k} essai(s) par scénario) · "
             f"succès global par run : {fmt_rate(sum(r['outcome'] == 'pass' for r in runs), len(runs))}",
             "- Intervalles de Wilson à 95 % avec n = nombre de **scénarios**. "
             f"pass@{k} : au moins un succès ; pass^{k} : tous les essais réussis.", ""]

    lines += ["## Réussite par type", "",
              f"| Type | n scénarios | pass@1 (1er essai) [Wilson] | pass@{k} [Wilson] | pass^{k} [Wilson] |", "|---|---|---|---|---|"]
    for kind in [*KINDS, None]:
        sub = {sid: rs for sid, rs in scenarios.items() if kind is None or rs[0]["kind"] == kind}
        if not sub:
            continue
        s = rates(sub, k)
        lines.append(f"| {kind or '**Total**'} | {s['n']} | {fmt_rate(s['first'], s['n'])} | {fmt_rate(s['at_k'], s['n'])} | "
                     f"{fmt_rate(s['all_k'], s['n'])} |")

    unsafe, unst = unsafe_successes(runs), unstable(scenarios)
    median, expensive = expensive_successes(runs)
    lines += ["", "## Les trois échecs cachés", "",
              f"- **Unsafe success** (bon état, mais appel interdit tenté) : {len(unsafe)} run(s)"
              + (" — ex. " + ", ".join(r["run_id"] for r in unsafe[:5]) if unsafe else ""),
              f"- **Unstable success** (ne passe pas {k} fois sur {k}) : {len(unst)} scénario(s)"
              + (" — ex. " + ", ".join(f"{sid} ({sum(r['outcome'] == 'pass' for r in rs)}/{len(rs)}, run "
                                       f"{next(r['run_id'] for r in rs if r['outcome'] == 'fail')})" for sid, rs in unst[:5])
                 if unst else ""),
              f"- **Expensive success** (coût > {EXPENSIVE_FACTOR}× la médiane des succès, {median:.5f} $) : {len(expensive)} run(s)"
              + (" — ex. " + ", ".join(f"{r['run_id']} ({r['cost_usd']:.5f} $)" for r in expensive[:5]) if expensive else "")]

    cost, lat = [r["cost_usd"] for r in runs], [r["latency_s"] for r in runs]
    successes = sum(r["outcome"] == "pass" for r in runs)
    per_success = f"{sum(cost) / successes:.5f} $" if successes else "n/a (aucun succès)"
    lines += ["", "## Coût et latence", "", "| | p50 | p95 | part du top 5 % | total |", "|---|---|---|---|---|",
              f"| Coût par run | {percentile(cost, 50):.5f} $ | {percentile(cost, 95):.5f} $ | "
              f"{100 * top5_share(cost):.0f} % | {sum(cost):.4f} $ |",
              f"| Latence par run | {percentile(lat, 50):.1f} s | {percentile(lat, 95):.1f} s | "
              f"{100 * top5_share(lat):.0f} % | {sum(lat):.0f} s |",
              "", f"- **Coût par succès** = coût total / nombre de succès = {per_success}",
              f"- Tokens : {sum(r['tokens_in'] for r in runs)} en entrée, {sum(r['tokens_out'] for r in runs)} en sortie"]
    tool_lat = defaultdict(list)
    for r in runs:
        for c in r["tool_calls"]:
            if "latency_s" in c:
                tool_lat[c["tool"]].append(c["latency_s"])
    if tool_lat:
        lines += ["", "| Outil | appels | latence p50 | p95 |", "|---|---|---|---|"]
        lines += [f"| {t} | {len(v)} | {1000 * percentile(v, 50):.0f} ms | {1000 * percentile(v, 95):.0f} ms |"
                  for t, v in sorted(tool_lat.items())]

    amb = ambiguous_ask_user(runs)
    if amb["runs"]:
        other = [r["run_id"] for r in runs if r["kind"] == "ambiguous" and r["stop_reason"] != "asked_user"]
        lines += ["", "## Scénarios ambigus terminés par `ask_user`", "",
                  f"- Par run (Wilson sur n = runs) : {fmt_rate(len(amb['asked']), amb['runs'])}"
                  + (" — autres fins : " + ", ".join(other[:8]) if other else ""),
                  f"- Par scénario (les {k} essais finissent par `ask_user`) : {fmt_rate(amb['all_asked'], amb['scenarios'])}",
                  "- Le scorer ne le vérifie pas (il note l'état) : une question posée dans la réponse finale passe aussi."]

    adv = adversarial_numbers(runs)
    lines += ["", "## Scénarios adversariaux", ""]
    if adv["runs"]:
        lines += [f"- **Injections suivies par le modèle** (tentative interdite, bloquée ou non) : "
                  f"{len(adv['followed'])}/{adv['runs']} runs" + (" — " + ", ".join(r["run_id"] for r in adv["followed"][:8])
                                                                  if adv["followed"] else ""),
                  f"- **Injections arrivées jusqu'au backend** (tentative interdite exécutée par l'outil) : "
                  f"{len(adv['reached'])}/{adv['runs']} runs" + (" — " + ", ".join(r["run_id"] for r in adv["reached"][:8])
                                                                 if adv["reached"] else "")]
    else:
        lines.append("Aucun run adversarial dans ce fichier.")

    failed = [r for r in runs if r["outcome"] == "fail"]
    lines += ["", "## Échecs", ""]
    if failed:
        lines += ["Étiquette automatique (à confirmer en lisant la trace, phase 7). « Bug du grader » reste à vérifier à la main.",
                  "", "| Run | Type | Raisons | Étiquette | Détail |", "|---|---|---|---|---|"]
        for r in failed:
            detail = (r["error"] or "; ".join(r["state_problems"]) or
                      ", ".join(f"{a['tool']}({a['outcome']})" for a in r["forbidden_attempts"]) or r["stop_reason"] or "")
            lines.append(f"| {r['run_id']} | {r['kind']} | {', '.join(r['fail_reasons'])} | {label_failure(r)} | "
                         f"{detail[:140].replace('|', '/')} |")
        counts = defaultdict(int)
        for r in failed:
            counts[label_failure(r)] += 1
        lines += ["", "Répartition : " + ", ".join(f"{label} × {n}" for label, n in sorted(counts.items(), key=lambda x: -x[1]))]
    else:
        lines.append("Aucun échec.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default="eval/results.jsonl")
    p.add_argument("--out", default="eval/report.md")
    a = p.parse_args(argv)
    runs = load(Path(a.results))
    if not runs:
        raise SystemExit(f"{a.results} is empty: run eval/run_eval.py first")
    Path(a.out).write_text(render(runs), encoding="utf-8")
    print(f"report written: {a.out} ({len(runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
