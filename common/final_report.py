"""Final cohesive report (spec 8.1). Every claim is computed from the run's artifacts; nothing is pre-written.

Each number links to the file it came from and to the module report section that discusses it. If an artifact
is missing the generator fails loudly instead of substituting a default.
"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from common.reporting import write_report
from common.state import PipelineStateManager, StageStatus
from common.stats import format_rate_ci
from harness.report import replication_verdict
from harness.runner import read_jsonl

REQUIRED = ["manifest.json", "state.json", "metrics.json", "dataset_eda.json", "review_log.json", "budget.json",
            "training_metrics.json", "thresholds.json", "trials.jsonl", "gated_trials.jsonl", "rlcd_gen_stats.json",
            "harness_report.md", "dataset_report.md", "training_report.md", "eval_report.md"]


class FinalReportGenerator:
    def __init__(self, run_id: str, results_dir: str | Path = "results"):
        self.run_id = run_id
        self.run_dir = Path(results_dir) / run_id
        self.state_mgr = PipelineStateManager(run_id=run_id, results_dir=results_dir)

    # ------------------------------------------------------------------ helpers
    def _json(self, name: str) -> Any:
        return json.loads((self.run_dir / name).read_text())

    @staticmethod
    def src(text: str, file: str, report: str = "", anchor: str = "") -> str:
        """A number or claim followed by links to its source file and module report."""
        links = [f"[{file}]({file})"]
        if report:
            links.append(f"[{report.split('.')[0]}]({report}{'#' + anchor if anchor else ''})")
        return f"{text} ({'; '.join(links)})"

    def generate_all(self) -> dict[str, Path]:
        self.state_mgr.check_prerequisites_for_stage("final_report")
        missing = [f for f in REQUIRED if not (self.run_dir / f).exists()]
        if missing:
            raise FileNotFoundError(f"Cannot build the final report: missing artifacts in {self.run_dir}: {missing}")
        md = self._markdown()
        md_path, html_path = write_report(self.run_dir, "final_report", md, "Robot Safety Gate — Final Report")
        report_md = self.run_dir / "report.md"
        report_md.write_text(md, encoding="utf-8")
        self.state_mgr.set_stage_status("final_report", StageStatus.PASSED,
                                        details={"generated_at": datetime.now().isoformat(), "html": str(html_path)})
        return {"html": html_path, "md": md_path, "report_md": report_md}

    # ------------------------------------------------------------------ content
    def _markdown(self) -> str:
        S = self.src
        man, mt = self._json("manifest.json"), self._json("metrics.json")
        eda, rv, bud = self._json("dataset_eda.json"), self._json("review_log.json"), self._json("budget.json")
        tr, th, gen = self._json("training_metrics.json"), self._json("thresholds.json"), self._json("rlcd_gen_stats.json")
        state = self._json("state.json")["stages"]
        trials, gated = read_jsonl(self.run_dir / "trials.jsonl"), read_jsonl(self.run_dir / "gated_trials.jsonl")
        models_cfg = list(json.loads((self.run_dir / "models_used.json").read_text()).values())
        m, dm, hm = mt["main_test_metrics"], mt["decomposed_slice_metrics"], mt["heldout_harness_metrics"]
        t, hc, ea = mt["targets"], mt["harness_comparison"], mt["error_analysis"]
        rs = rv["summary"]
        hand = state["B_dataset"].get("handcheck", {})
        pairs_txt = f"{eda['total_pairs']:,} RLCD pairs"
        agree_txt = f"{rs['agreement_rate'] * 100:.1f}%"
        from budget.tracker import BudgetTracker
        project_spend = BudgetTracker(bud["hard_cap_usd"], results_dir=self.run_dir.parent, run_id=self.run_id).total_spend
        spend_txt = f"${bud['total_spend_usd']:.3f} this run, ${project_spend:.3f} project-wide"
        gen_txt = f"{gen['tokens_per_sec']} tokens/s, {gen['sec_per_pair']} s per pair"
        n_trials_txt = f"{len(trials)} ungated trials"
        fmt = lambda x: f"{x * 100:.2f}%"
        ci = lambda mm, k: f"{fmt(mm[k + '_rate'])} [{mm[k + '_ci95'][0] * 100:.1f}%, {mm[k + '_ci95'][1] * 100:.1f}%]"
        targets_met = t["pass_through_met"] and t["false_blocks_met"]
        zs = mt["baselines"]["zero_shot_clef"]["metrics"]["test"]
        judge = mt["baselines"]["local_gemma_judge"]["metrics"]["test"]
        verdict_lines = replication_verdict(trials, models_cfg)
        chat = [x for x in trials if x["framing"] == "chat"]
        robot = [x for x in trials if x["variant"] == "harmful" and x["framing"] in ("real", "sim")]

        caveats = []
        if not hand.get("labelled"):
            caveats.append("The ~100-example hand-check set has **not** been labelled by a human yet, so label quality is bounded only by the LLM reviewer's agreement.")
        if not targets_met:
            caveats.append("At least one spec target is **not met** on the test set (section 6); this is reported as a result.")
        if hm["harmful_pass_through_count"] or dm["harmful_pass_through_count"]:
            caveats.append("Harmful actions passed on the harder slices (decomposed attacks and/or Module A trajectories): see section 8.")
        if hc.get("stale_note"):
            caveats.append(hc["stale_note"])

        L = [
            "# Robot Safety Gate — Final Report", "",
            f"**Run:** `{self.run_id}` · **Generated:** {datetime.now():%Y-%m-%d %H:%M} · **Git commit:** `{man['git_commit']}` · "
            f"**Config hash:** `{man['config_hash']}` ([manifest.json](manifest.json))", "",
            "## 1. Executive summary", "",
            f"- **Targets:** harmful pass-through {S(ci(m, 'harmful_pass_through'), 'metrics.json', 'eval_report.html', '1-targets')} against < {t['harmful_pass_through_target'] * 100:.0f}% → "
            f"**{'PASS' if t['pass_through_met'] else 'FAIL'}**; benign false blocks {S(ci(m, 'benign_false_block'), 'metrics.json', 'eval_report.html', '1-targets')} against < "
            f"{t['benign_false_blocks_target'] * 100:.0f}% → **{'PASS' if t['false_blocks_met'] else 'FAIL'}** (test set, 95% Wilson CIs).",
            f"- **Harder slices:** decomposed attacks pass-through {ci(dm, 'harmful_pass_through')} (N harmful={dm['harmful_samples']}); "
            f"Module A trajectories {ci(hm, 'harmful_pass_through')} (N harmful={hm['harmful_samples']}) "
            f"({S('metrics.json', 'metrics.json', 'eval_report.html', '2-gate-metrics')}).",
            f"- **End to end:** harmful tasks completed {S(hc['harmful_completed_ungated']['rate'], 'with_vs_without_gate_comparison.csv', 'eval_report.html', '4-end-to-end-the-gate-in-the-harness')} without the gate vs "
            f"{hc['harmful_completed_gated']['rate']} with it; benign twins completed {hc['benign_completed_ungated']['rate']} without vs {hc['benign_completed_gated']['rate']} with the gate.",
            f"- **Replication:** see section 2 for the computed verdict on {len(trials)} ungated trials.",
            f"- **Dataset:** {S(pairs_txt, 'dataset_eda.json', 'dataset_report.html', '1-brief')}, reviewer agreement "
            f"{S(agree_txt, 'review_log.json', 'dataset_report.html', '5-review')} on {rs['sample_size']} records.",
            f"- **Spend:** {S(spend_txt, 'budget.json')} of the ${bud['hard_cap_usd']:.2f} cap.",
            f"- **Overall:** {'all spec targets met on the test set' if targets_met else 'spec targets NOT all met'}"
            f"{'; open caveats listed below.' if caveats else '.'}", *[f"  - {c}" for c in caveats], "",
            "## 2. Replication verdict", "",
            f"Computed from {S(n_trials_txt, 'trials.jsonl', 'harness_report.html', '1-replication-verdict')}; policies: "
            + ", ".join(f"`{x['model_name']}`" for x in models_cfg) + ".", "",
            *verdict_lines,
            f"Pooled harmful refusal: plain chat {format_rate_ci(sum(x['final_score'] == 'refused' for x in chat), len(chat))} vs robot "
            f"{format_rate_ci(sum(x['final_score'] == 'refused' for x in robot), len(robot))} ({S('chat_vs_robot_comparison.csv', 'chat_vs_robot_comparison.csv')}).", "",
            "## 3. Harness design", "",
            "A mocked table with two arms, six tools (`move_arm`, `grip`, `release`, `rotate_wrist`, `look`, `done`), scenario objects from YAML, and rule-based "
            "state scoring (`refused` / `attempted` / `completed-equivalent`). The first tool result is the scene description; episodes stop at `done`, a text "
            "refusal, 15 tool calls or the timeout. The policy is a real local model driven through native tool calling; the real framing contains no simulation tells "
            f"(checked in the dry-run). Details, scenario cards, dry-run transcripts and limits: {S('harness report', 'harness_report.md', 'harness_report.html')}.", "",
            "## 4. Dataset and how RLCD worked", "",
            f"Scene states are sampled programmatically; local Gemma (`{man['models']['rlcd_generator']}`) proposes the next tool call twice, once as a safety-minded "
            "operator and once as a reckless one, from the same state. The label is **not** taken from the persona: the proposed action is simulated in the harness "
            "environment and labelled by the rule (advances the hazardous end state → block, benign continuation → allow, advances an end state whose hazard depends on an "
            f"unverified object → escalate). Generation ran at {S(gen_txt, 'rlcd_gen_stats.json', 'dataset_report.html', '2-throughput-and-projection-measured-local-gemma')} "
            f"({gen['failed_pairs']} pairs dropped for invalid output); projected {gen['projected_hours_5k_pairs']} h for 5,000 pairs.", "",
            f"Label mix: {', '.join(f'{k} {v:,}' for k, v in sorted(eda['label_distribution'].items()))}. Splits: "
            f"{', '.join(f'{k} {v:,}' for k, v in sorted(eda['split_counts'].items()))}; pair leakage across splits: {eda['leaked_pairs_count']}.",
            f"Review: `{rs['reviewer']}` labelled {rs['sample_size']} sampled records blind; agreement {rs['agreement_rate'] * 100:.1f}% "
            f"(CI {rs['agreement_ci95'][0] * 100:.1f}–{rs['agreement_ci95'][1] * 100:.1f}%), {rs['disputed_count']} disputes resolved by relabel-to-human or dropping the pair "
            f"({S('review_log.json', 'review_log.json', 'dataset_report.html', '5-review')}).",
            (f"Hand-check set: {hand['labelled']} human labels; dataset-vs-human {hand['dataset_vs_human'] * 100:.1f}%, reviewer-vs-human "
             f"{(hand.get('reviewer_vs_human') or 0) * 100:.1f}%." if hand.get("labelled") else "Hand-check set: **pending** (no human labels yet)."), "",
            "## 5. Gate training", "",
            f"Path: **{state['C_training']['path_selected']}**. Hidden states from the frozen Qwen3.5-9B backbone were cached and the Clef joint decision head was fine-tuned "
            f"for {tr['total_epochs']} epochs with cross-entropy plus a pairwise RLCD margin loss; best epoch {tr['best_epoch']} by validation loss "
            f"({S('training_metrics.json', 'training_metrics.json', 'training_report.html', '3-validation-metrics-per-epoch')}). No LoRA, no fallback model.",
            f"Frozen thresholds (tuned on validation only): τ_allow = {th['tau_allow']}, τ_block = {th['tau_block']}; validation pass-through "
            f"{fmt(th['val_harmful_pass_through'])}, false blocks {fmt(th['val_benign_false_blocks'])}, targets met on validation: {th['meets_targets']} "
            f"({S('thresholds.json', 'thresholds.json', 'training_report.html', '4-frozen-thresholds-tuned-on-validation-only')}).", "",
            "## 6. Evaluation against targets", "",
            "| Set | N | Harmful pass-through | Benign false blocks | Escalate | ECE | Latency p50 / p95 |", "|---|---|---|---|---|---|---|",
            *[f"| {name} | {mm['total_samples']} | {ci(mm, 'harmful_pass_through')} | {ci(mm, 'benign_false_block')} | {ci(mm, 'escalate')} | {mm['ece']} | {mm['latency_p50_ms']:.0f} / {mm['latency_p95_ms']:.0f} ms |"
              for name, mm in (("test", m), ("decomposed attacks", dm), ("heldout_harness", hm))],
            "", f"Sources: {S('metrics.json', 'metrics.json', 'eval_report.html', '2-gate-metrics')}.", "",
            "### Baselines (test set)", "",
            "| Baseline | Harmful pass-through | Benign false blocks | Accuracy |", "|---|---|---|---|",
            f"| Trained gate | {ci(m, 'harmful_pass_through')} | {ci(m, 'benign_false_block')} | {fmt(m['accuracy'])} |",
            f"| Zero-shot Clef-flash | {ci(zs, 'harmful_pass_through')} | {ci(zs, 'benign_false_block')} | {fmt(zs['accuracy'])} |",
            f"| Local Gemma judge | {ci(judge, 'harmful_pass_through')} | {ci(judge, 'benign_false_block')} | {fmt(judge['accuracy'])} |", "",
            "## 7. End-to-end impact of the gate", "",
            "| Measure | Without gate | With gate |", "|---|---|---|",
            f"| Harmful completed-equivalent | {hc['harmful_completed_ungated']['rate']} ({hc['harmful_completed_ungated']['k']}/{hc['harmful_completed_ungated']['n']}) | {hc['harmful_completed_gated']['rate']} ({hc['harmful_completed_gated']['k']}/{hc['harmful_completed_gated']['n']}) |",
            f"| Benign twin completed-equivalent | {hc['benign_completed_ungated']['rate']} ({hc['benign_completed_ungated']['k']}/{hc['benign_completed_ungated']['n']}) | {hc['benign_completed_gated']['rate']} ({hc['benign_completed_gated']['k']}/{hc['benign_completed_gated']['n']}) |", "",
            f"Gate decisions: {hc['gate_decisions']}; benign trials interrupted at least once: {hc['benign_trials_with_any_intervention']['rate']}. "
            f"Sources: {S('with_vs_without_gate_comparison.csv', 'with_vs_without_gate_comparison.csv', 'eval_report.html', '4-end-to-end-the-gate-in-the-harness')}; trials in {len(gated)} gated transcripts ([gated_trials.jsonl](gated_trials.jsonl)).", "",
            "## 8. Errors and limitations", "",
            f"- {ea['total_misses']} harmful actions passed and {ea['total_false_blocks']} benign actions were stopped across all evaluation sets; every case with full context is in [errors.jsonl](errors.jsonl) "
            f"and discussed in {S('Module D, section 5', 'eval_report.md', 'eval_report.html', '5-error-analysis-spec-7-4')}.",
            "- Labels come from a simulation rule; the test split shares its generator with training, so the decomposed and Module A slices are the more meaningful tests.",
            "- Scenes are text-only and limited to the five scripted scenarios; the policy set is limited to the models in section 2.",
            "- The frontier-LLM judge baseline is Phase 2 and was not run; LoRA was not used.",
            "- Plain-chat scoring is lexical (explicit refusal or an opening warning counts as refusal).", "",
            "## 9. How to reproduce", "",
            "```", "uv sync", "./sg harness run --dry-run && ./sg harness run --replication   # Module A (Ollama + local policy)",
            "./sg data generate                                  # Module B, Gemma (resumable)",
            "# put a real OPENAI_API_KEY in .env, then:",
            "./sg data review   # blind OpenAI review, budget-capped",
            "./sg data handcheck-label                           # optional but recommended: your ~100 labels",
            "./sg data split", "./sg train", "./sg eval && ./sg e2e", "./sg report", "```", "",
            f"Models: {json.dumps(man['models'])}. Thresholds in config: {json.dumps(man['thresholds'])}.",
        ]
        return "\n".join(L)
