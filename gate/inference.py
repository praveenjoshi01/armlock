"""Inference for the trained gate: frozen Clef backbone + fine-tuned head + frozen thresholds."""
import json
from pathlib import Path
from typing import Any, Optional

import torch

from common.exceptions import ModelUnavailableError
from gate.clef import ClefGate
from gate.decision import apply_decision_rule
from gate.template import build_clef_record


class SafetyGateInference:
    """The gate used in Module D. Every proposed action, including look(), is scored by the model."""

    def __init__(self, run_id: str, results_dir: Path | str = "results", history_window_n: int = 5,
                 use_pretrained_head: bool = False):
        run_dir = Path(results_dir) / run_id
        th_path, ck_path = run_dir / "thresholds.json", run_dir / "best_checkpoint.pt"
        if not use_pretrained_head:
            for p in (th_path, ck_path):
                if not p.exists():
                    raise ModelUnavailableError(f"{p} not found: Module C has not produced a gate for {run_id}.")
            th = json.loads(th_path.read_text())
            self.tau_allow, self.tau_block = th["tau_allow"], th["tau_block"]
            head_state = torch.load(ck_path)["head_state"]
            self._trained_state, self._trained_tau = head_state, (self.tau_allow, self.tau_block)
        else:  # zero-shot baseline: pretrained head, default thresholds
            self.tau_allow, self.tau_block = 0.90, 0.50
            head_state = None
            self._trained_state, self._trained_tau = None, (0.90, 0.50)
        self.head_kind = "zero_shot" if use_pretrained_head else "trained"
        self.gate = ClefGate(device="mps", head_state=head_state)
        self.history_window_n = history_window_n

    def use_head(self, kind: str) -> None:
        """'trained' (fine-tuned head + frozen thresholds) or 'zero_shot' (pretrained Clef head, default thresholds)."""
        if kind == "zero_shot":
            self.gate.set_head(None)
            self.tau_allow, self.tau_block = 0.90, 0.50
        else:
            self.gate.set_head(self._trained_state)
            self.tau_allow, self.tau_block = self._trained_tau
        self.head_kind = kind

    def close(self) -> None:
        import gc
        del self.gate
        gc.collect()
        torch.mps.empty_cache()

    def score(self, records: list[dict[str, Any]]) -> tuple[list[list[float]], list[float]]:
        """records: dataset-style dicts. Returns [p_allow, p_block, p_escalate] per record and seconds per record."""
        clef = [build_clef_record(r["id"], r["instruction"], r["scene"], r["history"], r["proposed_action"],
                                  self.history_window_n) for r in records]
        return self.gate.probabilities(clef)

    def evaluate_proposed_action(self, instruction: str, scene: str, history: list[dict[str, Any]],
                                 proposed_action: dict[str, Any]) -> dict[str, Any]:
        probs, secs = self.score([{"id": "live", "instruction": instruction, "scene": scene,
                                   "history": history, "proposed_action": proposed_action}])
        p = probs[0]
        return {"decision": apply_decision_rule(p[0], p[1], p[2], self.tau_allow, self.tau_block),
                "p_allow": p[0], "p_block": p[1], "p_escalate": p[2], "latency_sec": secs[0],
                "tau_allow": self.tau_allow, "tau_block": self.tau_block}
