import pytest

from eval.metrics import compute_brier_score, compute_ece, compute_safety_metrics, wilson_score_interval


def test_wilson_score_interval():
    lo, hi = wilson_score_interval(0, 100)
    assert lo == 0.0 and 0.0 < hi < 0.05
    assert wilson_score_interval(0, 0) == (0.0, 0.0)
    lo, hi = wilson_score_interval(50, 100)
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61


def test_ece_and_brier():
    assert compute_ece([1.0, 1.0], [True, True]) == 0.0
    assert compute_ece([1.0, 1.0], [False, False]) == 1.0
    assert compute_brier_score([[1.0, 0.0, 0.0]], [0]) == 0.0
    assert compute_brier_score([[0.0, 1.0, 0.0]], [0]) == 2.0


def _row(true, dec, p):
    return {"true_label": true, "decision": dec, "p_allow": p[0], "p_block": p[1], "p_escalate": p[2], "latency_ms": 10.0}


def test_safety_metrics_follow_spec_definitions():
    rows = [_row("block", "allow", [0.95, 0.03, 0.02]),   # harmful pass-through
            _row("block", "block", [0.05, 0.9, 0.05]),
            _row("allow", "escalate", [0.6, 0.3, 0.1]),    # benign false block (escalate counts)
            _row("allow", "allow", [0.97, 0.02, 0.01]),
            _row("escalate", "escalate", [0.1, 0.2, 0.7])]
    m = compute_safety_metrics(rows)
    assert m["harmful_pass_through_count"] == 1 and m["harmful_pass_through_rate"] == 0.5
    assert m["benign_false_block_count"] == 1 and m["benign_false_block_rate"] == 0.5
    assert m["escalate_count"] == 2 and m["target_pass_through_met"] is False
    assert m["latency_p50_ms"] == 10.0 and m["reliability_bins"]


def test_calibration_uses_probabilities_not_thresholded_decision():
    # argmax is right but decision (fail-closed) escalated: calibration must still count it as correct
    m = compute_safety_metrics([_row("allow", "escalate", [0.8, 0.1, 0.1])])
    assert m["reliability_bins"][0]["accuracy"] == 1.0
