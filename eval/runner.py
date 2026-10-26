import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from common.reporting import write_report
from common.state import PipelineStateManager, StageStatus
from common.stats import format_rate_ci
from eval.gated_harness import GatedHarnessRunner, rate
from eval.offline import OfflineEvaluator
from harness.runner import read_jsonl


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def ci(m: dict[str, Any], key: str) -> str:
    lo, hi = m[f"{key}_ci95"]
    rate_key = key if key == "accuracy" else key + "_rate"
    return f"{pct(m[rate_key])} [{lo * 100:.1f}%, {hi * 100:.1f}%]"


class EvaluationRunner:
    """Module D: (1) offline metrics, (2) gated harness trials, (3) error analysis, (4) report."""

    def __init__(self, config: dict[str, Any], run_id: str, results_dir: Path | str = "results"):
        self.config, self.run_id = config, run_id
        self.results_dir = Path(results_dir)
        self.run_dir = self.results_dir / run_id
        self.state_mgr = PipelineStateManager(run_id=run_id, results_dir=results_dir)

    def run_full_evaluation(self, trials_per_condition: Optional[int] = None,
                            on_trial_callback: Optional[Callable[[dict[str, Any]], None]] = None,
                            on_progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
                            rerun_ungated: Optional[bool] = None) -> dict[str, Any]:
        self.state_mgr.check_prerequisites_for_stage("D_evaluation")
        self.state_mgr.check_gated_harness_prerequisites()
        self.state_mgr.set_stage_status("D_evaluation", StageStatus.RUNNING, details={"last_run": datetime.now().isoformat()})
        try:
            evaluator = OfflineEvaluator(self.config, self.run_id, self.results_dir)
            offline = evaluator.cached() or evaluator.evaluate_all(on_progress_callback)
            if on_progress_callback:
                on_progress_callback({"step": "gated_harness", "message": "Gated harness trials (every tool call passes the gate)"})
            harness = GatedHarnessRunner(self.config, self.run_id, self.results_dir).run_gated_replication(
                trials_per_condition, on_trial_callback, rerun_ungated)
            th = self.config["thresholds"]
            m = offline["main_test_metrics"]
            metrics = {
                "run_id": self.run_id, "timestamp": datetime.now().isoformat(), **offline,
                "harness_comparison": harness,
                "targets": {
                    "harmful_pass_through_target": th["target_max_harmful_pass_through"],
                    "benign_false_blocks_target": th["target_max_benign_false_blocks"],
                    "harmful_pass_through_achieved": m["harmful_pass_through_rate"],
                    "benign_false_blocks_achieved": m["benign_false_block_rate"],
                    "harmful_pass_through_ci95": m["harmful_pass_through_ci95"],
                    "benign_false_blocks_ci95": m["benign_false_block_ci95"],
                    "pass_through_met": m["harmful_pass_through_rate"] < th["target_max_harmful_pass_through"],
                    "false_blocks_met": m["benign_false_block_rate"] < th["target_max_benign_false_blocks"],
                },
            }
            (self.run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
            reports = self._report(metrics)
        except Exception as e:
            self.state_mgr.set_stage_status("D_evaluation", StageStatus.FAILED, error=f"{type(e).__name__}: {e}")
            raise
        t = metrics["targets"]
        self.state_mgr.set_stage_status("D_evaluation", StageStatus.PASSED, details={
            "gated_replication_passed": True, "metrics_file": str(self.run_dir / "metrics.json"),
            "gated_trials_file": str(self.run_dir / "gated_trials.jsonl"),
            "comparison_csv": str(self.run_dir / "with_vs_without_gate_comparison.csv"),
            "report_html": str(reports[1]), "targets_met": t["pass_through_met"] and t["false_blocks_met"],
            "last_run": datetime.now().isoformat()})
        self.state_mgr.set_stage_status("final_report", StageStatus.READY)
        return {"status": "passed", "metrics": metrics, "reports": {"md": str(reports[0]), "html": str(reports[1])},
                "harness_res": harness}

    # ------------------------------------------------------------------------------------------
    def _metric_table(self, rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
        out = ["| Set | N | Harmful pass-through | Benign false blocks | Escalate | Accuracy | ECE | Brier | Latency p50 / p95 |",
               "|---|---|---|---|---|---|---|---|---|"]
        for name, m in rows:
            cal = (f"{m['ece']:.3f} | {m['brier_score']:.3f}" if m.get("ece") is not None else "n/a | n/a")
            out.append(f"| {name} | {m['total_samples']} | {ci(m, 'harmful_pass_through')} ({m['harmful_pass_through_count']}/{m['harmful_samples']}) | "
                       f"{ci(m, 'benign_false_block')} ({m['benign_false_block_count']}/{m['benign_samples']}) | {ci(m, 'escalate')} | "
                       f"{ci(m, 'accuracy')} | {cal} | {m['latency_p50_ms']:.0f} / {m['latency_p95_ms']:.0f} ms |")
        return out

    def _report(self, mt: dict[str, Any]):
        t, hc, ea = mt["targets"], mt["harness_comparison"], mt["error_analysis"]
        b = mt["baselines"]
        verdict = lambda ok: "PASS" if ok else "FAIL"
        ungated = read_jsonl(self.run_dir / "trials.jsonl")
        gated = read_jsonl(self.run_dir / "gated_trials.jsonl")

        def grid(framing_axis: str) -> list[str]:
            rows = ["| Model | Condition | Ungated completed | Gated completed | Ungated refused | Gated refused |", "|---|---|---|---|---|---|"]
            for mid in sorted({x["model_id"] for x in gated}):
                for variant in ("harmful", "benign_twin"):
                    for fr in (("real", "sim") if framing_axis == "framing" else ("real",)):
                        u = [x for x in ungated if (x["model_id"], x["variant"], x["framing"]) == (mid, variant, fr)]
                        g = [x for x in gated if (x["model_id"], x["variant"], x["framing"]) == (mid, variant, fr)]
                        c = lambda xs, lab: format_rate_ci(sum(1 for x in xs if x["final_score"] == lab), len(xs))
                        rows.append(f"| `{mid}` | {variant} / {fr} | {c(u, 'completed-equivalent')} | {c(g, 'completed-equivalent')} | {c(u, 'refused')} | {c(g, 'refused')} |")
            return rows

        chat = [x for x in ungated if x["framing"] == "chat"]
        robot_h = [x for x in ungated if x["variant"] == "harmful" and x["framing"] in ("real", "sim")]
        md = [
            "# Module D — Evaluation Report", "",
            f"**Run ID:** `{self.run_id}` · **Date:** {datetime.now():%Y-%m-%d %H:%M}", "",
            "## 1. Targets", "",
            "| Target | Achieved (test set, 95% Wilson CI) | Result |", "|---|---|---|",
            f"| Harmful pass-through < {t['harmful_pass_through_target'] * 100:.0f}% | {pct(t['harmful_pass_through_achieved'])} "
            f"[{t['harmful_pass_through_ci95'][0] * 100:.1f}%, {t['harmful_pass_through_ci95'][1] * 100:.1f}%] | **{verdict(t['pass_through_met'])}** |",
            f"| Benign false blocks < {t['benign_false_blocks_target'] * 100:.0f}% | {pct(t['benign_false_blocks_achieved'])} "
            f"[{t['benign_false_blocks_ci95'][0] * 100:.1f}%, {t['benign_false_blocks_ci95'][1] * 100:.1f}%] | **{verdict(t['false_blocks_met'])}** |", "",
            "A point estimate under target is not proof of meeting it: read the CI upper bound. A missed target is reported as a result, not hidden.", "",
            "## 2. Gate metrics", "",
            f"Trained head with the frozen thresholds tau_allow = {b['trained_gate']['thresholds'][0]}, tau_block = {b['trained_gate']['thresholds'][1]}.", "",
            *self._metric_table([("test", mt["main_test_metrics"]), ("decomposed attacks", mt["decomposed_slice_metrics"]),
                                 ("heldout_harness (Module A trajectories)", mt["heldout_harness_metrics"])]
                                + ([("hard negatives (held-out templates)", mt["hard_negatives_metrics"])] if "hard_negatives_metrics" in mt else [])),
            "", "### Per scenario (test set)", "", *self._metric_table(list(mt["per_scenario_metrics"].items())),
            "", "### Reliability (test set)", "", "| Confidence bin | N | Mean confidence | Accuracy |", "|---|---|---|---|",
            *[f"| {r['bin']} | {r['n']} | {r['confidence']} | {r['accuracy']} |" for r in mt["main_test_metrics"]["reliability_bins"]],
            "", "## 3. Baselines (Spec 7.2)", "",
            "| Baseline | Set | Harmful pass-through | Benign false blocks | Accuracy | Latency p50 |", "|---|---|---|---|---|---|",
            *[f"| {v['name']} | {s} | {ci(m, 'harmful_pass_through')} | {ci(m, 'benign_false_block')} | {pct(m['accuracy'])} | {m['latency_p50_ms']:.0f} ms |"
              for v in b.values() for s, m in v["metrics"].items()],
            "", "The frontier-LLM judge baseline is Phase 2 only and was not run.", "",
            "## 4. End-to-end: the gate in the harness", "",
            f"Same conditions and seeds as the ungated replication ({hc['total_gated_trials']} gated robot trials; chat trials have no tool executor to gate).",
            *([f"> **Warning:** {hc['stale_note']}"] if hc.get("stale_note") else []), "",
            "| Measure | Without gate | With gate |", "|---|---|---|",
            f"| Harmful completed-equivalent | {hc['harmful_completed_ungated']['rate']} ({hc['harmful_completed_ungated']['k']}/{hc['harmful_completed_ungated']['n']}) | "
            f"{hc['harmful_completed_gated']['rate']} ({hc['harmful_completed_gated']['k']}/{hc['harmful_completed_gated']['n']}) |",
            f"| Benign twin completed-equivalent | {hc['benign_completed_ungated']['rate']} ({hc['benign_completed_ungated']['k']}/{hc['benign_completed_ungated']['n']}) | "
            f"{hc['benign_completed_gated']['rate']} ({hc['benign_completed_gated']['k']}/{hc['benign_completed_gated']['n']}) |", "",
            f"Gate decisions during trials: {hc['gate_decisions']}; benign trials with at least one block/escalate: {hc['benign_trials_with_any_intervention']['rate']}; "
            f"live gate latency p50 {hc['gate_latency_p50_ms']} ms.", "",
            "### Real vs sim framing, with and without the gate", "", *grid("framing"),
            "", "### Chat vs robot (ungated refusal)", "",
            "| Condition | Refused (95% CI) |", "|---|---|",
            f"| Plain chat, harmful | {format_rate_ci(sum(x['final_score'] == 'refused' for x in chat), len(chat))} |",
            f"| Robot (real+sim), harmful | {format_rate_ci(sum(x['final_score'] == 'refused' for x in robot_h), len(robot_h))} |",
            "", "## 5. Error analysis (Spec 7.4)", "",
            f"{ea['total_misses']} harmful actions passed and {ea['total_false_blocks']} benign actions were blocked or escalated across all sets "
            f"(full list with context in `{ea['errors_file']}`).", "",
            "### Decomposed attacks by step", "", "| Step | Records | Harmful steps caught | Innocent steps blocked |", "|---|---|---|---|",
            *[f"| {k} | {v['n']} | {v['block_caught']}/{v['block_true']} | {v['allow_blocked']}/{v['allow_true']} |" for k, v in ea["decomposed_by_step"].items()],
            "", "### Top misses (harmful actions the gate allowed)", "",
        ]
        for e in ea["misses"][:10]:
            md += [f"- `{e['id']}` ({e['set']}, {e['scenario']}, p(allow/block/escalate)={e['p']}): *{e['instruction']}* — scene: {e['scene']} — "
                   f"history {len(e['history'])} steps — proposed `{json.dumps(e['proposed_action'])}`"]
        md += ["", "### Top false blocks (benign actions the gate stopped)", ""]
        for e in ea["false_blocks"][:10]:
            md += [f"- `{e['id']}` ({e['set']}, {e['scenario']}, decision {e['decision']}, p={e['p']}): *{e['instruction']}* — proposed `{json.dumps(e['proposed_action'])}`"]
        md += ["", "## 6. Limitations", "",
               "- Labels for every evaluation set come from the simulation rule in Module B; the test split shares its generator with training, so the held-out `heldout_harness` and `decomposed` slices are the harder tests.",
               "- Gemma-as-judge returns hard decisions only, so it has no calibration numbers.",
               "- Policy coverage is limited to the models listed in the Module A report."]
        return write_report(self.run_dir, "eval_report", "\n".join(md), "Module D — Evaluation Report")
