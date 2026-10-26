import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from common.reporting import write_report
from common.state import PipelineStateManager, StageStatus
from gate import clef
from gate.trainer import GateTrainer


class GateTrainingRunner:
    """Module C orchestration: prerequisites, CONFIRM check, training, thresholds, report, state."""

    def __init__(self, config: dict[str, Any], run_id: str = "run_002", results_dir: Path | str = "results"):
        self.config = config
        self.run_id = run_id
        self.results_dir = Path(results_dir)
        self.run_dir = self.results_dir / run_id
        self.state_mgr = PipelineStateManager(run_id=run_id, results_dir=results_dir)

    def check_confirm_path(self) -> dict[str, Any]:
        """Spec 6.3 CONFIRM, answered from the files on disk. Raises if Clef-flash is not usable."""
        info = clef.confirm_report()
        info.update({
            "path_type": "clef_flash_decision_head",
            "path_label": "Clef-flash decision head (frozen Qwen3.5-9B backbone, head fine-tuned)",
            "device": "Apple Silicon MPS (bf16 backbone, fp32 head training)",
            "fallback_used": False,
            "details": f"{info['interface']} Head config {info['head_config']}, {info['head_tensors']} tensors.",
        })
        return info

    def run_training_pipeline(self, on_progress_callback: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        self.state_mgr.check_prerequisites_for_stage("C_training")
        self.state_mgr.set_stage_status("C_training", StageStatus.RUNNING)
        try:
            confirm = self.check_confirm_path()
            res = GateTrainer(self.config, self.run_id, self.results_dir).train(on_progress_callback)
            reports = self._report(res, confirm)
        except Exception as e:
            self.state_mgr.set_stage_status("C_training", StageStatus.FAILED, error=f"{type(e).__name__}: {e}")
            raise
        t = res["threshold_tuning"]
        self.state_mgr.set_stage_status("C_training", StageStatus.PASSED, details={
            "path_selected": confirm["path_label"], "best_checkpoint": res["best_checkpoint"],
            "tau_allow": t["tau_allow"], "tau_block": t["tau_block"],
            "val_harmful_pass_through": t["harmful_pass_through"], "val_benign_false_blocks": t["benign_false_blocks"],
            "val_escalate_rate": t["escalate_rate"], "val_meets_targets": t["meets_targets"],
            "report_html": str(reports[1]), "last_run": datetime.now().isoformat()})
        res["confirm_info"], res["reports"] = confirm, {"md": str(reports[0]), "html": str(reports[1])}
        return res

    def _report(self, r: dict[str, Any], confirm: dict[str, Any]):
        t, zs, th = r["threshold_tuning"], r["zero_shot_val"], self.config["thresholds"]
        c = r["config"]
        md = [
            "# Module C — Gate Training Report", "",
            f"**Run ID:** `{self.run_id}` · **Date:** {datetime.now():%Y-%m-%d %H:%M}", "",
            f"> **Training path: {confirm['path_label']}.** No fallback was used.", "",
            "## 1. CONFIRM check (spec 6.3)", "",
            f"- Interface: {confirm['interface']}",
            f"- Head: `{json.dumps(confirm['head_config'])}`, {confirm['head_tensors']} tensors, loaded with `strict=True`.",
            f"- Snapshot: `{confirm['snapshot']}`",
            f"- MPS available: {confirm['mps_available']}; the 9B backbone runs in bf16 on MPS (reference PyTorch kernels for linear attention).",
            "- LoRA: **not used.** The backbone is frozen and only the decision head is trained (optional per spec 6.3); LoRA would "
            "need a backbone forward/backward per step and was out of budget on this machine.", "",
            "## 2. Configuration", "",
            f"- Train records {r['n_train']:,}, validation records {r['n_val']:,}; history window N = {c['history_window_n']}",
            f"- Epochs {c['epochs']}, batch = {c['batch_pairs']} pairs, grad-accum {c['grad_accum']}, lr {c['lr']}, weight decay {c['weight_decay']}, head dropout {c['dropout']}",
            f"- Loss: C1 cross-entropy (sqrt-inverse-frequency class weights) + {c['lambda_rlcd']} × C2 pairwise RLCD margin loss (margin {c['margin']})",
            "- Memory: Gemma is unloaded before the backbone loads; the backbone is unloaded after its hidden states are cached.", "",
            "## 3. Validation metrics per epoch", "",
            f"Decision rule at the default thresholds (τ_allow {th['tau_allow']}, τ_block {th['tau_block']}).", "",
            "| Epoch | Train loss | Val loss | Val acc (argmax) | Harmful pass-through | Benign false blocks | Escalate rate | Seconds |",
            "|---|---|---|---|---|---|---|---|",
            f"| 0 (pretrained head, zero-shot) | – | – | – | {zs['harmful_pass_through'] * 100:.2f}% | {zs['benign_false_blocks'] * 100:.2f}% | {zs['escalate_rate'] * 100:.2f}% | – |",
            *[f"| {e['epoch']} | {e['train_loss']} | {e['val_loss']} | {e['val_accuracy'] * 100:.1f}% | {e['harmful_pass_through'] * 100:.2f}% | "
              f"{e['benign_false_blocks'] * 100:.2f}% | {e['escalate_rate'] * 100:.2f}% | {e['epoch_duration_sec']} |" for e in r["history"]["epoch_metrics"]],
            "", f"Best epoch by validation loss: **{r['best_epoch']}**.", "",
            "## 4. Frozen thresholds (tuned on validation only)", "",
            f"- τ_allow = **{t['tau_allow']}**, τ_block = **{t['tau_block']}**",
            f"- Validation harmful pass-through **{t['harmful_pass_through'] * 100:.2f}%** (target < {th['target_max_harmful_pass_through'] * 100:.0f}%), "
            f"benign false blocks **{t['benign_false_blocks'] * 100:.2f}%** (target < {th['target_max_benign_false_blocks'] * 100:.0f}%), escalate rate {t['escalate_rate'] * 100:.2f}%",
            f"- Targets met on validation: **{'PASS' if t['meets_targets'] else 'FAIL'}**. Validation is drawn from the training distribution, so these are optimistic; "
            "Module D reports the held-out test, decomposed and harness slices.", "",
            "## 5. Checkpoints", "",
            "| Epoch | File | Val loss | Size (MB) |", "|---|---|---|---|",
            *[f"| {k['epoch']} | `{k['filename']}` | {k['val_loss']} | {k['size_mb']} |" for k in r["history"]["checkpoints"]],
            f"| best | `best_checkpoint.pt` (epoch {r['best_epoch']}) | | |", "",
            "## 6. Runtime and hardware", "",
            f"- Machine: {platform.platform()}; device MPS",
            f"- Backbone caching: {r['backbone_cache_time_sec']} s; training wall time {r['total_training_time_sec']} s; peak RSS {r['peak_rss_gb']} GB",
        ]
        return write_report(self.run_dir, "training_report", "\n".join(md), "Module C — Gate Training Report")
