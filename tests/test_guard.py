import json

from gate.guard import BLOCK_MESSAGE, ESCALATE_MESSAGE, ArmGuard, summarise_audit, verify_audit

ACTION = {"tool": "release", "args": {"arm": "left"}}


class Scorer:
    def __init__(self, decision="allow", boom=False):
        self.decision, self.boom = decision, boom

    def evaluate_proposed_action(self, instruction, scene, history, proposed_action):
        if self.boom:
            raise TimeoutError("model unavailable")
        p = {"allow": 0.95, "block": 0.03, "escalate": 0.02} if self.decision == "allow" else {"allow": 0.1, "block": 0.8, "escalate": 0.1}
        return {"decision": self.decision, "p_allow": p["allow"], "p_block": p["block"], "p_escalate": p["escalate"]}


def run(guard, calls):
    executed = []
    f = guard.wrap(lambda a: executed.append(a) or {"success": True, "message": "done"})
    return [f("i", "s", [], ACTION) for _ in range(calls)], executed


def test_allow_executes_and_block_does_not(tmp_path):
    out, ex = run(ArmGuard(Scorer("allow")), 1)
    assert out[0]["success"] and ex == [ACTION]
    out, ex = run(ArmGuard(Scorer("block")), 1)
    assert not out[0]["success"] and out[0]["message"] == BLOCK_MESSAGE and ex == []


def test_scorer_failure_fails_closed_and_is_logged(tmp_path):
    g = ArmGuard(Scorer(boom=True), audit_path=tmp_path / "a.jsonl")
    out, ex = run(g, 1)
    assert ex == [] and out[0]["guard"].error.startswith("TimeoutError")
    assert summarise_audit(tmp_path / "a.jsonl")["fail_closed_events"] == 1


def test_escalate_defaults_to_refusal_and_can_be_approved():
    out, ex = run(ArmGuard(Scorer("escalate")), 1)
    assert ex == [] and out[0]["message"] == ESCALATE_MESSAGE
    approved = []
    g = ArmGuard(Scorer("escalate"), on_escalate=lambda ctx, res: approved.append(ctx["proposed_action"]) or True)
    out, ex = run(g, 1)
    assert ex == [ACTION] and approved == [ACTION]
    g = ArmGuard(Scorer("escalate"), on_escalate=lambda ctx, res: (_ for _ in ()).throw(RuntimeError("operator offline")))
    out, ex = run(g, 1)
    assert ex == [] and "operator offline" in out[0]["guard"].error


def test_audit_chain_detects_tampering_and_survives_restart(tmp_path):
    path = tmp_path / "a.jsonl"
    run(ArmGuard(Scorer("allow"), audit_path=path), 3)
    run(ArmGuard(Scorer("block"), audit_path=path), 2)  # a new guard continues the same chain
    assert verify_audit(path) == (True, None)
    lines = path.read_text().splitlines()
    forged = json.loads(lines[3])  # first entry written by the blocking guard
    forged["decision"], forged["allowed"] = "allow", True
    lines[3] = json.dumps(forged)
    path.write_text("\n".join(lines) + "\n")
    assert verify_audit(path) == (False, 3)
    assert summarise_audit(path)["chain_ok"] is False


def test_summary_counts(tmp_path):
    path = tmp_path / "a.jsonl"
    run(ArmGuard(Scorer("allow"), audit_path=path), 3)
    run(ArmGuard(Scorer("block"), audit_path=path), 1)
    s = summarise_audit(path)
    assert (s["decisions"], s["allow"], s["block"], s["chain_ok"]) == (4, 3, 1, True)
