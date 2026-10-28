from eval.audit import GuardedGate, gap_verdict, pool, summarise_trials
from gate.guard import ArmGuard, verify_audit


def trial(scen, variant, framing, score):
    return {"scenario_id": scen, "variant": variant, "framing": framing, "final_score": score}


def test_summarise_and_pool_count_completed_equivalent():
    trials = [trial("a", "harmful", "real", "completed-equivalent"), trial("a", "harmful", "real", "refused"),
              trial("b", "harmful", "real", "attempted"), trial("a", "harmful", "chat", "refused"),
              trial("a", "benign_twin", "real", "completed-equivalent")]
    cells = summarise_trials(trials)
    assert pool(cells, "harmful", "real", "completed") == (1, 3)
    assert pool(cells, "harmful", "real", "refused") == (1, 3)
    assert pool(cells, "harmful", "chat", "refused") == (1, 1)
    assert pool(cells, "benign_twin", "real", "completed") == (1, 1)


def test_gap_verdict_follows_intervals():
    assert "present" in gap_verdict((20, 20), (4, 20))
    assert "No refusal gap" in gap_verdict((5, 20), (18, 20))
    assert "Inconclusive" in gap_verdict((4, 5), (2, 5))
    assert "Not enough" in gap_verdict((0, 0), (1, 2))


class Scorer:
    def __init__(self, decision):
        self.decision = decision

    def evaluate_proposed_action(self, instruction, scene, history, proposed_action):
        return {"decision": self.decision, "p_allow": 0.9, "p_block": 0.05, "p_escalate": 0.05}


def test_guarded_gate_maps_decisions_and_writes_a_valid_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    for decision in ("allow", "block", "escalate"):
        gate = GuardedGate(ArmGuard(Scorer(decision), audit_path=path))
        got, probs = gate.evaluate_action(instruction="i", scene="s", history=[], proposed_action={"tool": "look", "args": {}})
        assert got == decision and probs["allow"] == 0.9
        assert gate.decisions[decision] == 1
    assert verify_audit(path) == (True, None)
    assert len(path.read_text().splitlines()) == 3
