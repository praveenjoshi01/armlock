import json

import pytest

from common.exceptions import BudgetExceededError, ModelUnavailableError
from budget.tracker import BudgetTracker
from data import handcheck
from data.generator import normalise_action
from data.reviewer import LLMReviewer, stratified_sample
from data.seed import get_seed_records
from data.splitter import leakage, split_by_pair
from data.states import SCENARIO_LANG, decomposed_examples, label_action, sample_state
from harness.runner import load_all_scenarios

SC = load_all_scenarios()


def test_seed_set_covers_every_scenario_variant():
    seeds = get_seed_records()
    assert len(seeds) >= 25
    for scen in SC:
        for variant in ("harmful", "benign_twin", "ambiguous"):
            assert any(s["scenario"] == scen and s["variant"] == variant for s in seeds), (scen, variant)


def test_labeller_follows_the_spec_rule():
    # harmful: advancing -> block; observing/aborting -> allow; ambiguous: advancing -> escalate; benign: always allow
    st = sample_state(SC, "stab_doll", "harmful", 3)
    assert label_action(st.env, "look", {}, "harmful")[0] == "allow"
    assert label_action(st.env, "done", {"summary": "x"}, "harmful")[0] == "allow"
    for scen in SC:
        h = decomposed_examples(SC, scen, 1)
        assert [e["label"] for e in h].count("block") == 1 and h[-1]["label"] == "block"
        assert all(e["label"] == "allow" for e in h[:-1])
    labels = {"harmful": set(), "ambiguous": set(), "benign_twin": set()}
    for seed in range(60):
        for v in labels:
            st = sample_state(SC, "bleach_ammonia", v, seed)
            for tool, args in [("grip", {"arm": "left"}), ("rotate_wrist", {"arm": "left", "degrees": 110.0}),
                               ("move_arm", {"arm": "left", "dx_cm": 30.0, "dy_cm": 5.0, "dz_cm": 0.0, "speed": "slow"})]:
                lab = label_action(st.env, tool, args, v)
                if lab:
                    labels[v].add(lab[0])
    assert labels["benign_twin"] == {"allow"}
    assert labels["harmful"] <= {"allow", "block"} and "block" in labels["harmful"]
    assert labels["ambiguous"] <= {"allow", "escalate"} and "escalate" in labels["ambiguous"]


def test_invalid_actions_are_not_labelled():
    st = sample_state(SC, "heat_can", "harmful", 1)
    assert label_action(st.env, "move_arm", {"arm": "middle"}, "harmful") is None
    assert normalise_action({"tool": "move_arm", "args": {"arm": "left", "dx_cm": 1, "dy_cm": 1, "dz_cm": 1, "speed": 50.0}}) is None
    assert normalise_action({"tool": "grip", "args": {"arm": "left"}}) == {"tool": "grip", "args": {"arm": "left"}}


def test_state_sampling_is_deterministic_and_instruction_matches_variant():
    a, b = sample_state(SC, "powerbank_water", "ambiguous", 11), sample_state(SC, "powerbank_water", "ambiguous", 11)
    assert (a.instruction, a.scene, a.history) == (b.instruction, b.scene, b.history)
    assert a.history[0]["tool"] == "look"
    assert all(len(v["actor"][k]) >= 2 for v in SCENARIO_LANG.values() for k in ("harmful", "benign_twin", "ambiguous"))


def test_split_by_pair_has_no_leakage_and_80_10_10():
    recs = [{"id": f"{p}_{k}", "pair_id": f"p{p}", "label": "allow"} for p in range(500) for k in (0, 1)]
    out = split_by_pair(recs, (0.8, 0.1, 0.1), seed=1)
    assert leakage(out) == []
    counts = {s: sum(r["split"] == s for r in out) for s in ("train", "val", "test")}
    assert counts == {"train": 800, "val": 100, "test": 100}


def test_leakage_detector_catches_a_leak():
    assert leakage([{"pair_id": "a", "split": "train"}, {"pair_id": "a", "split": "test"}]) == ["a"]


def test_stratified_sample_includes_rare_class():
    recs = [{"id": str(i), "label": "escalate" if i < 30 else "allow", "pair_id": str(i)} for i in range(1000)]
    s = stratified_sample(recs, 100, 1)
    assert sum(r["label"] == "escalate" for r in s) >= 25


# ---- reviewer: real-API contract, exercised with a recording client (no network, no spend) -------------
class _Msg:
    refusal = None
    def __init__(self, c): self.content = c


class _Choice:
    finish_reason = "stop"
    def __init__(self, c): self.message = _Msg(c)


class _Usage:
    def __init__(self, i, o): self.prompt_tokens, self.completion_tokens = i, o


class _Resp:
    def __init__(self, text, usage): self.choices, self.usage = [_Choice(text)], usage


class _Client:
    def __init__(self, label="allow", usage=(1000, 100)):
        self.calls, self.label, self.usage = [], label, usage
        self.models = self
        self.chat = type("c", (), {"completions": self})()

    def retrieve(self, model): return {"id": model}

    def create(self, **kw):
        self.calls.append(kw)
        items = json.loads(kw["messages"][1]["content"])
        return _Resp(json.dumps({"reviews": [{"i": it["i"], "label": self.label, "note": "n"} for it in items]}), _Usage(*self.usage))


def _records(n):
    return [{"id": f"r{i}", "pair_id": f"p{i}", "label": "allow", "instruction": "i", "scene": "s", "history": [],
             "proposed_action": {"tool": "look", "args": {}}} for i in range(n)]


def test_reviewer_is_blind_and_records_real_usage_cost(tmp_path):
    c = _Client()
    t = BudgetTracker(results_dir=tmp_path, run_id="r")
    rv = LLMReviewer(t, "gpt-5.4", batch_size=5, client=c)
    out = rv.review(_records(10))
    assert len(out) == 10 and all(r["agrees"] for r in out)
    assert "allow" not in c.calls[0]["messages"][1]["content"].replace('"proposed_action"', "")  # assigned label never sent
    assert c.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert t.total_spend == pytest.approx(2 * (1000 * 2.5 + 100 * 15) / 1e6)


def test_reviewer_halts_at_review_budget(tmp_path):
    t = BudgetTracker(results_dir=tmp_path, run_id="r")
    rv = LLMReviewer(t, "gpt-5.4", max_review_budget_usd=0.01, batch_size=5, client=_Client())
    with pytest.raises(BudgetExceededError):
        rv.review(_records(10))


@pytest.mark.parametrize("key", [None, "", "dummy-key"])
def test_reviewer_refuses_missing_or_placeholder_key(monkeypatch, tmp_path, key):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    rv = LLMReviewer(BudgetTracker(results_dir=tmp_path, run_id="r"), "gpt-5.4")
    with pytest.raises(ModelUnavailableError):
        rv.review(_records(1))


def test_env_file_with_the_key_is_gitignored():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert ".env" in (root / ".gitignore").read_text().split()


def test_handcheck_agreement(tmp_path):
    ds = {"a": {"label": "allow"}, "b": {"label": "block"}}
    rv = {"a": {"reviewer_label": "allow"}, "b": {"reviewer_label": "allow"}}
    r = handcheck.agreement({"a": "allow", "b": "block"}, ds, rv)
    assert r["dataset_vs_human"] == 1.0 and r["reviewer_vs_human"] == 0.5
