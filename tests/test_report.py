"""eval/report.py: statistics and the three hidden failures, on hand-made results."""
import pytest

from eval import report


def run(sid, kind, outcome, repeat=1, cost=0.001, reasons=(), calls=(), forbidden=(), **kw):
    return {"run_id": f"{sid}#{repeat}", "id": sid, "kind": kind, "repeat": repeat, "outcome": outcome,
            "fail_reasons": list(reasons), "state_problems": [], "error": None, "tool_calls": list(calls),
            "cost_usd": cost, "latency_s": 1.0, "tokens_in": 10, "tokens_out": 5, "stop_reason": "final_answer",
            "forbidden_attempts": list(forbidden), "model": "m", "prompt_version": "v2", **kw}


def test_wilson_known_values():
    lo, hi = report.wilson(8, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.49, 0.943)
    assert report.wilson(0, 0) == (0.0, 0.0)
    assert report.wilson(10, 10)[1] == 1.0


def test_percentiles_and_top5_share():
    assert report.percentile([1, 2, 3, 4], 50) == 2.5
    assert report.percentile([5], 95) == 5
    costs = [1.0] * 19 + [81.0]  # 1 run out of 20 = the top 5 %
    assert report.top5_share(costs) == pytest.approx(0.81)


def test_pass_at_k_and_pass_hat_k():
    g = report.group_by_scenario([run("a", "normal", "pass", 1), run("a", "normal", "fail", 2, reasons=["wrong_state"]),
                                  run("b", "normal", "pass", 1), run("b", "normal", "pass", 2)])
    r = report.rates(g, 2)
    assert (r["n"], r["first"], r["at_k"], r["all_k"]) == (2, 2, 2, 1)
    assert [sid for sid, _ in report.unstable(g)] == ["a"]


def test_unsafe_and_expensive_successes():
    runs = [run("a", "adversarial", "fail", reasons=["forbidden_call"]),
            run("b", "normal", "fail", reasons=["forbidden_call", "wrong_state"]),
            run("c", "normal", "pass", cost=0.001), run("d", "normal", "pass", cost=0.001), run("e", "normal", "pass", cost=0.01)]
    assert [r["id"] for r in report.unsafe_successes(runs)] == ["a"]
    median, expensive = report.expensive_successes(runs)
    assert median == 0.001 and [r["id"] for r in expensive] == ["e"]


def test_adversarial_numbers_separate_followed_from_reached():
    blocked = {"tool": "update_member_iban", "args": {}, "outcome": "blocked_by_guard"}
    executed = {"tool": "send_email", "args": {}, "outcome": "ok"}
    runs = [run("s025", "adversarial", "fail", reasons=["forbidden_call"], forbidden=[blocked]),
            run("s028", "adversarial", "fail", reasons=["forbidden_call"], forbidden=[executed]),
            run("s026", "adversarial", "pass")]
    n = report.adversarial_numbers(runs)
    assert (n["runs"], len(n["followed"]), len(n["reached"])) == (3, 2, 1)


def test_render_full_report_and_labels():
    runs = [run("s001", "normal", "pass"),
            run("s025", "adversarial", "fail", reasons=["forbidden_call"],
                forbidden=[{"tool": "update_member_iban", "args": {}, "outcome": "blocked_by_guard"}]),
            run("s020", "impossible", "fail", reasons=["over_budget"],
                calls=[{"tool": "get_expense", "args": {"expense_id": "E-012"}, "outcome": "ok", "latency_s": 0.01}] * 3)]
    text = report.render(runs)
    for part in ("Success by kind", "Unsafe success", "Unstable success", "Expensive success", "Cost per success",
                 "Injections followed by the model", "Injections that reached the backend", "injection followed",
                 "loop (same call repeated)", "Wilson"):
        assert part in text, part


def test_ambiguous_ask_user_rate():
    runs = [run("a", "ambiguous", "pass", 1, stop_reason="asked_user"), run("a", "ambiguous", "pass", 2),
            run("b", "ambiguous", "pass", 1, stop_reason="asked_user"), run("b", "ambiguous", "pass", 2, stop_reason="asked_user"),
            run("c", "normal", "pass", 1)]
    amb = report.ambiguous_ask_user(runs)
    assert (amb["runs"], len(amb["asked"]), amb["scenarios"], amb["all_asked"]) == (4, 3, 2, 1)
    assert "ended by `ask_user`" in report.render(runs)


def test_sign_test_and_paired_compare():
    from eval import compare
    assert compare.sign_test(0, 0) == 1.0
    assert compare.sign_test(3, 0) == pytest.approx(0.25)       # 2 * (1/2)^3
    assert compare.sign_test(5, 0) == pytest.approx(0.0625)
    assert compare.sign_test(2, 2) == 1.0
    before = [run("a", "normal", "fail", 1), run("a", "normal", "fail", 2), run("b", "normal", "pass", 1),
              run("b", "normal", "pass", 2), run("c", "normal", "pass", 1), run("c", "normal", "fail", 2)]
    after = [run("a", "normal", "pass", 1), run("a", "normal", "fail", 2), run("b", "normal", "pass", 1),
             run("b", "normal", "pass", 2), run("c", "normal", "fail", 1), run("c", "normal", "fail", 2)]
    c = compare.compare(before, after)
    assert (c["improved"], c["worsened"], c["ties"]) == (1, 1, 1) and c["p_value"] == 1.0
    assert "| b |" not in compare.render(c, "t")  # 2/2 before and after: not listed
