"""Integration seam: put the gate between a policy's tool calls and the robot's executor.

    guard = ArmGuard(scorer, audit_path="audit.jsonl")
    safe_execute = guard.wrap(robot.execute_tool)
    result = safe_execute(instruction, scene, history, {"tool": "release", "args": {"arm": "left"}})

Behaviour a buyer can rely on:

* **Fails closed.** If the scorer raises or times out, the action is blocked and the failure is logged.
* **Every decision is logged** to an append-only JSONL audit trail. Each line carries the hash of the previous
  line, so editing or deleting an entry is detectable with ``verify_audit``.
* **Block and escalate are explicit.** A blocked call returns a message the policy can act on; an escalated call
  is handed to ``on_escalate`` (a human, a supervisor process, a safe-stop routine). With no handler, escalate
  is treated as block, which is the safe default.
"""
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

BLOCK_MESSAGE = "Action refused by safety controller."
ESCALATE_MESSAGE = "Action held for human confirmation."


class Scorer(Protocol):
    def evaluate_proposed_action(self, instruction: str, scene: str, history: list[dict[str, Any]],
                                 proposed_action: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class GuardResult:
    decision: str                       # allow | block | escalate
    allowed: bool                       # True only if the action may execute now
    message: str                        # what the policy / operator is told
    probs: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: Optional[str] = None         # set when the scorer failed and the guard failed closed
    audit_hash: str = ""


class ArmGuard:
    def __init__(self, scorer: Scorer, audit_path: Optional[Path | str] = None,
                 on_escalate: Optional[Callable[[dict[str, Any], GuardResult], bool]] = None,
                 deployment: str = "default"):
        """
        on_escalate(context, result) -> True if a human/supervisor approves the action, False to refuse it.
        """
        self.scorer = scorer
        self.audit_path = Path(audit_path) if audit_path else None
        self.on_escalate = on_escalate
        self.deployment = deployment
        self._prev_hash = self._last_hash()

    # ------------------------------------------------------------------ decision
    def decide(self, instruction: str, scene: str, history: list[dict[str, Any]],
               proposed_action: dict[str, Any]) -> GuardResult:
        t0 = time.perf_counter()
        ctx = {"instruction": instruction, "scene": scene, "history": history, "proposed_action": proposed_action}
        try:
            r = self.scorer.evaluate_proposed_action(instruction, scene, history, proposed_action)
            decision = r["decision"]
            if decision not in ("allow", "block", "escalate"):
                raise ValueError(f"scorer returned unknown decision {decision!r}")
            probs = {k: round(float(r[f"p_{k}"]), 4) for k in ("allow", "block", "escalate") if f"p_{k}" in r}
            result = GuardResult(decision, decision == "allow",
                                 {"allow": "", "block": BLOCK_MESSAGE, "escalate": ESCALATE_MESSAGE}[decision], probs)
        except Exception as e:  # fail closed: an unscored action is never executed
            result = GuardResult("block", False, BLOCK_MESSAGE, error=f"{type(e).__name__}: {e}")
        if result.decision == "escalate":
            approved = False
            if self.on_escalate is not None:
                try:
                    approved = bool(self.on_escalate(ctx, result))
                except Exception as e:
                    result.error = f"escalation handler failed: {type(e).__name__}: {e}"
            result.allowed = approved
            result.message = "" if approved else ESCALATE_MESSAGE
            result.decision = "escalate_approved" if approved else "escalate"
        result.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        result.audit_hash = self._log(ctx, result)
        return result

    def wrap(self, executor: Callable[[dict[str, Any]], Any]) -> Callable[..., Any]:
        """Return execute(instruction, scene, history, action) that runs `executor(action)` only if allowed."""
        def guarded(instruction: str, scene: str, history: list[dict[str, Any]], action: dict[str, Any]):
            res = self.decide(instruction, scene, history, action)
            if not res.allowed:
                return {"success": False, "message": res.message, "guard": res}
            return executor(action)
        return guarded

    # --------------------------------------------------------------------- audit
    def _last_hash(self) -> str:
        if self.audit_path and self.audit_path.exists():
            lines = self.audit_path.read_text().splitlines()
            if lines:
                return json.loads(lines[-1])["hash"]
        return "0" * 64

    def _log(self, ctx: dict[str, Any], res: GuardResult) -> str:
        entry = {"ts": time.time(), "deployment": self.deployment, "instruction": ctx["instruction"],
                 "scene": ctx["scene"], "history_len": len(ctx["history"]), "action": ctx["proposed_action"],
                 "decision": res.decision, "allowed": res.allowed, "probs": res.probs,
                 "latency_ms": res.latency_ms, "error": res.error, "prev": self._prev_hash}
        digest = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        entry["hash"] = digest
        self._prev_hash = digest
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return digest


def verify_audit(path: Path | str) -> tuple[bool, Optional[int]]:
    """Check the hash chain. Returns (ok, index_of_first_bad_line)."""
    prev = "0" * 64
    for i, line in enumerate(Path(path).read_text().splitlines()):
        entry = json.loads(line)
        claimed = entry.pop("hash")
        if entry["prev"] != prev or hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest() != claimed:
            return False, i
        prev = claimed
    return True, None


def summarise_audit(path: Path | str) -> dict[str, Any]:
    """One-glance numbers for a safety review: decisions, fail-closed events, latency."""
    rows = [json.loads(l) for l in Path(path).read_text().splitlines()]
    lat = sorted(r["latency_ms"] for r in rows)
    count = lambda d: sum(r["decision"] == d for r in rows)
    return {"decisions": len(rows), "allow": count("allow"), "block": count("block"), "escalate": count("escalate"),
            "escalate_approved": count("escalate_approved"), "fail_closed_events": sum(bool(r["error"]) for r in rows),
            "latency_p50_ms": lat[len(lat) // 2] if lat else None, "latency_p95_ms": lat[int(len(lat) * 0.95)] if lat else None,
            "chain_ok": verify_audit(path)[0]}
