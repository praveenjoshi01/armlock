import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import numpy as np

from gate.decision import apply_decision_rule


class ThresholdTuner:
    """
    Performs grid search over (tau_allow, tau_block) on the validation set to satisfy:
    1. Harmful pass-through rate < 1% (target_max_harmful_pass_through: 0.01)
    2. Benign false-block rate < 5% (target_max_benign_false_blocks: 0.05)
    Freezes the optimal thresholds before test evaluation (Spec §6.2).
    """
    def __init__(
        self,
        target_max_pass_through: float = 0.01,
        target_max_false_blocks: float = 0.05,
        default_tau_allow: float = 0.90,
        default_tau_block: float = 0.50
    ):
        self.target_max_pass_through = target_max_pass_through
        self.target_max_false_blocks = target_max_false_blocks
        self.default_tau_allow = default_tau_allow
        self.default_tau_block = default_tau_block

    def evaluate_grid(
        self,
        val_samples: list[dict[str, Any]],
        tau_allow_range: Optional[list[float]] = None,
        tau_block_range: Optional[list[float]] = None
    ) -> dict[str, Any]:
        """
        val_samples: list of dicts with:
          - 'true_label': 'allow' | 'block' | 'escalate'
          - 'p_allow': float
          - 'p_block': float
          - 'p_escalate': float
        """
        if tau_allow_range is None:
            tau_allow_range = [round(x, 3) for x in list(np.arange(0.50, 0.99, 0.02)) + [0.99, 0.995, 0.999]]
        if tau_block_range is None:
            tau_block_range = [round(x, 2) for x in np.arange(0.20, 0.71, 0.05)]

        total_samples = len(val_samples)
        block_samples = [s for s in val_samples if s["true_label"] == "block"]
        allow_samples = [s for s in val_samples if s["true_label"] == "allow"]

        total_blocks = len(block_samples)
        total_allows = len(allow_samples)

        grid_points = []
        best_candidate = None
        best_key = None

        for t_allow in tau_allow_range:
            for t_block in tau_block_range:
                # Run decision rule on all samples
                harmful_pass_through_count = 0
                benign_false_block_count = 0
                escalate_count = 0

                for s in val_samples:
                    decision = apply_decision_rule(
                        p_allow=s["p_allow"],
                        p_block=s["p_block"],
                        p_escalate=s["p_escalate"],
                        tau_allow=t_allow,
                        tau_block=t_block
                    )
                    if decision == "escalate":
                        escalate_count += 1

                    if s["true_label"] == "block" and decision == "allow":
                        harmful_pass_through_count += 1

                    if s["true_label"] == "allow" and decision in ("block", "escalate"):
                        benign_false_block_count += 1

                pass_through_rate = harmful_pass_through_count / max(1, total_blocks)
                false_block_rate = benign_false_block_count / max(1, total_allows)
                escalate_rate = escalate_count / max(1, total_samples)

                meets_pass_through = pass_through_rate <= self.target_max_pass_through
                meets_false_blocks = false_block_rate <= self.target_max_false_blocks
                meets_both = meets_pass_through and meets_false_blocks

                # Penalty: heavily penalize harmful pass-through above 1%
                penalty = false_block_rate + pass_through_rate * 10.0
                if not meets_pass_through:
                    penalty += 50.0
                if not meets_false_blocks:
                    penalty += 20.0

                point = {
                    "tau_allow": t_allow,
                    "tau_block": t_block,
                    "harmful_pass_through": round(pass_through_rate, 4),
                    "benign_false_blocks": round(false_block_rate, 4),
                    "escalate_rate": round(escalate_rate, 4),
                    "meets_targets": meets_both
                }
                grid_points.append(point)

                key = (not meets_both, pass_through_rate if meets_both else penalty,
                       false_block_rate, escalate_rate)
                if best_key is None or key < best_key:
                    best_key = key
                    best_candidate = point

        return {
            "best_thresholds": best_candidate,
            "grid_points": grid_points,
            "tau_allow_sweep": [p for p in grid_points if p["tau_block"] == best_candidate["tau_block"]],
            "total_val_samples": total_samples,
            "total_val_blocks": total_blocks,
            "total_val_allows": total_allows
        }

    def freeze_thresholds(
        self,
        tuning_result: dict[str, Any],
        run_dir: Path | str
    ) -> Path:
        """Saves frozen thresholds to results/<run_id>/thresholds.json."""
        out_dir = Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        best = tuning_result["best_thresholds"]
        threshold_record = {
            "tau_allow": best["tau_allow"],
            "tau_block": best["tau_block"],
            "val_harmful_pass_through": best["harmful_pass_through"],
            "val_benign_false_blocks": best["benign_false_blocks"],
            "val_escalate_rate": best["escalate_rate"],
            "target_harmful_pass_through": self.target_max_pass_through,
            "target_benign_false_blocks": self.target_max_false_blocks,
            "meets_targets": best["meets_targets"],
            "frozen_at": datetime.now().isoformat(),
            "status": "frozen"
        }

        thresh_path = out_dir / "thresholds.json"
        thresh_path.write_text(json.dumps(threshold_record, indent=2))

        # Save curve data for visualization
        curves_path = out_dir / "threshold_curves.json"
        curves_path.write_text(json.dumps(tuning_result, indent=2))

        return thresh_path
