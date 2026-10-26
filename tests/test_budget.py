import pytest
from common.exceptions import BudgetExceededError
from budget.tracker import BudgetTracker


def test_budget_spend_and_remaining(tmp_path):
    tracker = BudgetTracker(hard_cap_usd=5.00, results_dir=tmp_path, run_id="test_run")
    assert tracker.total_spend == 0.0
    assert tracker.remaining == 5.00

    tracker.record_spend(cost_usd=1.25, stage="B_review", provider="openai", model="gpt-5.4", notes="Review batch 1")
    assert tracker.total_spend == 1.25
    assert tracker.remaining == 3.75

    # Check persistence
    reloaded = BudgetTracker(hard_cap_usd=5.00, results_dir=tmp_path, run_id="test_run")
    assert reloaded.total_spend == 1.25
    assert len(reloaded.data["transactions"]) == 1


def test_budget_hard_cap_halt(tmp_path):
    tracker = BudgetTracker(hard_cap_usd=5.00, results_dir=tmp_path, run_id="test_run_halt")
    tracker.record_spend(cost_usd=4.50, stage="B_review", provider="openai", model="gpt-5.4")

    # Attempting to spend $0.60 when remaining is $0.50 must raise BudgetExceededError
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.record_spend(cost_usd=0.60, stage="B_review", provider="openai", model="gpt-5.4")

    assert exc_info.value.cap == 5.00
    assert tracker.total_spend == 4.50  # Spend did not increase after halt


def test_cap_is_project_wide_across_runs(tmp_path):
    a = BudgetTracker(hard_cap_usd=5.0, results_dir=tmp_path, run_id="run_a")
    a.record_spend(cost_usd=3.0, stage="B_review", provider="openai", model="gpt-5.4")
    b = BudgetTracker(hard_cap_usd=5.0, results_dir=tmp_path, run_id="run_b")
    assert b.total_spend == 3.0
    with pytest.raises(BudgetExceededError):
        b.record_spend(cost_usd=2.5, stage="B_review", provider="openai", model="gpt-5.4")


def test_unknown_model_pricing_is_an_error(tmp_path):
    t = BudgetTracker(results_dir=tmp_path, run_id="r")
    with pytest.raises(KeyError):
        t.estimate_cost("some-unpriced-model", 1000, 1000)
