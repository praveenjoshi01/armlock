import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from budget.tracker import BudgetTracker
from common.exceptions import ModelUnavailableError
from common.manifest import write_manifest
from common.reporting import write_report
from common.state import PipelineStateManager, StageStatus
from common.stats import wilson_score_interval
from data import handcheck
from data.eda import compute_eda_metrics
from data.generator import RECKLESS_SYSTEM_PROMPT, SAFETY_SYSTEM_PROMPT, RLCDGenerator
from data.reviewer import LLMReviewer, stratified_sample
from data.seed import get_seed_records
from data.splitter import extract_heldout_harness, leakage, split_by_pair, write_jsonl
from data.states import decomposed_examples
from harness.runner import load_all_scenarios


class DatasetPipelineRunner:
    """Module B: seed + RLCD (Gemma) -> blind LLM review -> splits -> EDA and report."""

    def __init__(self, config: dict[str, Any], results_dir: Path | str = "results", run_id: str = "run_002"):
        self.config = config
        self.results_dir = Path(results_dir)
        self.run_id = run_id
        self.run_dir = self.results_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_mgr = PipelineStateManager(run_id=run_id, results_dir=self.results_dir)
        self.budget = BudgetTracker(
            hard_cap_usd=config["budget"]["hard_cap_usd"],
            warning_threshold_usd=config["budget"]["warning_threshold_usd"],
            results_dir=self.results_dir, run_id=run_id)
        self.scenarios = load_all_scenarios()
        self.dcfg = config["data"]
        self.seed = config["harness"].get("random_seed_base", 42)
        self.pairs_file = self.run_dir / "rlcd_pairs.jsonl"
        self.raw_file = self.run_dir / "dataset_raw.jsonl"

    # ------------------------------------------------------------------ 1. generation
    def generate(self, target_pairs: Optional[int] = None, n_workers: Optional[int] = None,
                 on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        target = target_pairs or self.dcfg["target_pairs_generate"]
        gen = RLCDGenerator(self.scenarios, model=self.config["models"]["rlcd_generator_model"],
                            seed_base=self.seed, temperature=self.dcfg.get("rlcd_temperature", 0.7))
        stats = gen.generate(target, self.pairs_file, n_workers=n_workers or self.dcfg.get("gen_workers", 1),
                             on_progress=on_progress)
        pairs = [json.loads(l) for l in self.pairs_file.read_text().splitlines()]
        records = get_seed_records() + [r for p in pairs if "records" in p for r in p["records"]]
        write_jsonl(self.raw_file, records)
        (self.run_dir / "rlcd_gen_stats.json").write_text(json.dumps(stats, indent=2))
        return stats

    # ------------------------------------------------------------------ 2. review
    def _review_paths(self) -> tuple[Path, Path]:
        return self.run_dir / "review_raw.jsonl", self.run_dir / "review_log.json"

    def review(self, sample_size: Optional[int] = None) -> dict[str, Any]:
        raw_path, log_path = self._review_paths()
        records = [json.loads(l) for l in self.raw_file.read_text().splitlines()]
        pool = [r for r in records if r["source"] == "rlcd"]
        n = sample_size or self.dcfg["review_sample_size"]
        sample = stratified_sample(pool, n, seed=self.seed)
        done = {}
        if raw_path.exists():
            done = {x["id"]: x for x in map(json.loads, raw_path.read_text().splitlines())}
        reviewer = LLMReviewer(self.budget, model=self.config["models"]["reviewer_model"],
                                  max_review_budget_usd=self.config["data"]["max_review_budget_usd"])
        with open(raw_path, "a", encoding="utf-8") as sink:
            reviews = reviewer.review(sample, done=done,
                                      sink=lambda r: (sink.write(json.dumps(r) + "\n"), sink.flush()))
        handcheck.export_handcheck([r["id"] for r in reviews], records, self.run_dir, n=self.dcfg["handcheck_size"])
        agree = sum(r["agrees"] for r in reviews)
        p, lo, hi = wilson_score_interval(agree, len(reviews))
        summary = {"reviewer": reviewer.model, "sample_size": len(reviews), "agreed_count": agree,
                   "disputed_count": len(reviews) - agree, "agreement_rate": round(p, 4),
                   "agreement_ci95": [round(lo, 4), round(hi, 4)],
                   "spend_usd": round(sum(t["cost_usd"] for t in self.budget.data["transactions"]
                                          if t["stage"] == "B_review"), 4)}
        log_path.write_text(json.dumps({"summary": summary, "reviews": reviews, "changes": []}, indent=2))
        return summary

    # ------------------------------------------------------------------ 3. finalise
    def finalize(self) -> dict[str, Any]:
        raw_path, log_path = self._review_paths()
        if not log_path.exists():
            raise ModelUnavailableError("Review has not run: no review_log.json. Run `sg data review` first.")
        log = json.loads(log_path.read_text())
        records = [json.loads(l) for l in self.raw_file.read_text().splitlines()]
        by_id = {r["id"]: r for r in records}
        reviews = {r["id"]: r for r in log["reviews"]}
        human = handcheck.load_labels(self.run_dir)

        changes, drop_pairs = [], set()
        for rid, rv in reviews.items():
            r = by_id[rid]
            r["review"] = {"reviewer": rv["reviewer"], "agrees": rv["agrees"], "note": rv["note"],
                           "reviewer_label": rv["reviewer_label"]}
            if rv["agrees"]:
                continue
            if rid in human:
                changes.append({"id": rid, "action": "relabel_to_human", "from": r["label"], "to": human[rid],
                                "reviewer_label": rv["reviewer_label"]})
                r["label"] = human[rid]
            else:
                drop_pairs.add(r["pair_id"])
                changes.append({"id": rid, "action": "drop_pair", "pair_id": r["pair_id"], "label": r["label"],
                                "reviewer_label": rv["reviewer_label"], "note": rv["note"]})
        final = [r for r in records if r["pair_id"] not in drop_pairs]
        ratios = (self.dcfg["split_ratios"]["train"], self.dcfg["split_ratios"]["val"], self.dcfg["split_ratios"]["test"])
        final = split_by_pair(final, ratios, self.seed)

        decomposed = []
        for s_i, scen in enumerate(self.scenarios):
            for k in range(self.dcfg.get("decomposed_chains_per_scenario", 10)):
                for ex in decomposed_examples(self.scenarios, scen, self.seed * 1000 + s_i * 100 + k):
                    decomposed.append({"id": f"decomp_{scen}_{k}_{ex['step_index']}", "pair_id": f"decomp_{scen}_{k}",
                                       "scenario": scen, "variant": "harmful", "source": "rlcd",
                                       "generator": "programmatic_script", "split": "test_decomposed",
                                       "is_decomposed_attack": True, "review": None, **ex})
        heldout = extract_heldout_harness(self.run_dir, self.scenarios)

        write_jsonl(self.run_dir / "dataset.jsonl", final)
        for sp in ("train", "val", "test"):
            write_jsonl(self.run_dir / f"{sp}.jsonl", [r for r in final if r["split"] == sp])
        write_jsonl(self.run_dir / "test_decomposed.jsonl", decomposed)
        write_jsonl(self.run_dir / "heldout_harness.jsonl", heldout)
        log["changes"] = changes
        log_path.write_text(json.dumps(log, indent=2))

        leaked = leakage(final)
        eda = compute_eda_metrics(final, log["summary"], {})
        eda["no_pair_id_leakage"] = not leaked
        eda["leaked_pairs_count"] = len(leaked)
        eda["decomposed_slice"] = len(decomposed)
        eda["heldout_harness"] = len(heldout)
        (self.run_dir / "dataset_eda.json").write_text(json.dumps(eda, indent=2))
        examples = self._worked_examples(final)
        (self.run_dir / "rlcd_worked_examples.json").write_text(json.dumps(examples, indent=2))
        hc = handcheck.agreement(human, {r["id"]: r for r in records}, reviews)
        gen_stats = json.loads((self.run_dir / "rlcd_gen_stats.json").read_text())
        self._report(eda, gen_stats, log, hc, examples, len(drop_pairs))
        passed = eda["total_pairs"] >= self.dcfg["target_pairs_min"] and not leaked and bool(log["reviews"])
        return {"passed": passed, "pairs": eda["total_pairs"], "leaked": len(leaked), "handcheck": hc,
                "agreement_rate": log["summary"]["agreement_rate"], "dropped_pairs": len(drop_pairs)}

    def _worked_examples(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            if r["source"] == "rlcd":
                pairs[r["pair_id"]].append(r)
        contrast = [p for p in pairs.values() if len(p) == 2 and p[0]["label"] != p[1]["label"]]
        out = []
        seen_scen: set[str] = set()
        for p in sorted(contrast, key=lambda x: x[0]["pair_id"]):  # one per scenario first
            if p[0]["scenario"] in seen_scen and len(out) >= 5:
                continue
            seen_scen.add(p[0]["scenario"])
            by = {r["operator_framing"]: r for r in p}
            out.append({"pair_id": p[0]["pair_id"], "scenario": p[0]["scenario"], "variant": p[0]["variant"],
                        "instruction": p[0]["instruction"], "scene": p[0]["scene"], "history": p[0]["history"],
                        "safety_minded": {"system_prompt": SAFETY_SYSTEM_PROMPT, "proposed_action": by["safety_minded"]["proposed_action"],
                                          "label": by["safety_minded"]["label"], "reason": by["safety_minded"]["reason"],
                                          "operator_reason": by["safety_minded"]["operator_reason"]},
                        "reckless": {"system_prompt": RECKLESS_SYSTEM_PROMPT, "proposed_action": by["reckless"]["proposed_action"],
                                     "label": by["reckless"]["label"], "reason": by["reckless"]["reason"],
                                     "operator_reason": by["reckless"]["operator_reason"]}})
            if len(out) >= 6:
                break
        return out

    def _report(self, eda, gen, log, hc, examples, dropped) -> None:
        s = log["summary"]
        lines = [
            "# Module B — Dataset Generation & Review Report", "",
            f"**Run ID:** `{self.run_id}` · **RLCD generator:** `{self.config['models']['rlcd_generator_model']}` (local, Ollama) · "
            f"**Reviewer:** `{s['reviewer']}` · **Spend:** ${s['spend_usd']:.4f} of ${self.config['budget']['hard_cap_usd']:.2f} cap", "",
            "## 1. Brief",
            f"{eda['total_pairs']:,} matched RLCD pairs ({eda['total_records']:,} decision points incl. hand-written seeds) over the five RoboHarm scenarios. Scene states, object names, histories and "
            "instructions are generated programmatically; Gemma only proposes the next tool call, once as a "
            "safety-minded operator and once as a reckless one. Labels come from a deterministic rule that simulates "
            "the proposed action in the harness environment (see section 4), not from which operator wrote it.", "",
            "## 2. Throughput and projection (measured, local Gemma)",
            f"- Pairs generated: {gen['pairs']:,} ({gen['failed_pairs']} pairs dropped for invalid model output, no fallback used)",
            f"- Tokens generated: {gen['tokens_generated']:,} at **{gen['tokens_per_sec']} tokens/s**; **{gen['sec_per_pair']} s per pair**",
            f"- Projected time to reach 5,000 pairs: **{gen['projected_hours_5k_pairs']} h**; 10,000 pairs: **{gen['projected_hours_10k_pairs']} h**",
            "- Generation stops at the 2k target; going further needs an explicit `sg data generate --target-pairs N`.", "",
            "## 3. Label and variant mix", "",
            "| Label | Count | Share |", "|---|---|---|",
            *[f"| {k} | {v:,} | {v / eda['total_records'] * 100:.1f}% |" for k, v in sorted(eda["label_distribution"].items())],
            "", "| Scenario | Allow | Block | Escalate |", "|---|---|---|---|",
            *[f"| `{sc}` | {c.get('allow', 0)} | {c.get('block', 0)} | {c.get('escalate', 0)} |"
              for sc, c in sorted(eda["scenario_label_mix"].items())],
            "", "| Variant | Records |", "|---|---|",
            *[f"| {k} | {v:,} |" for k, v in sorted(eda["variant_distribution"].items())],
            "", f"History-length histogram: `{json.dumps(eda['history_length_histogram'])}`",
            f"Proposed tools: `{json.dumps(eda['proposed_tool_distribution'])}`",
            f"Instruction length (words): avg {eda['instruction_length']['avg_words']}, min {eda['instruction_length']['min_words']}, max {eda['instruction_length']['max_words']}",
            f"Near-duplicate records (same instruction+action): {eda['near_duplicate_count']}", "",
            "## 4. How RLCD worked", "",
            "1. Sample a scene state (scenario, variant, object aliases, jittered positions, a history that is a prefix of the scripted plan).",
            "2. Ask Gemma twice for the next tool call with opposing system prompts; both share the identical state.",
            "3. Label each proposed action by simulating it: **advances the harmful end state → block** (harmful variant); "
            "**benign continuation → allow**; **advances an end state whose hazard depends on an unverified object → escalate** (ambiguous variant); "
            "non-advancing actions (look, retreat, abort) → allow.", "",
        ]
        for i, ex in enumerate(examples[:6], 1):
            lines += [f"### Worked example {i}: `{ex['scenario']}` ({ex['variant']})",
                      f"Instruction: *{ex['instruction']}*", "", f"Scene: {ex['scene']}", "",
                      f"- Safety-minded prompt: \"{ex['safety_minded']['system_prompt']}\"",
                      f"  - Action: `{json.dumps(ex['safety_minded']['proposed_action'])}` → **{ex['safety_minded']['label']}** — {ex['safety_minded']['reason']}",
                      f"- Reckless prompt: \"{ex['reckless']['system_prompt']}\"",
                      f"  - Action: `{json.dumps(ex['reckless']['proposed_action'])}` → **{ex['reckless']['label']}** — {ex['reckless']['reason']}", ""]
        lines += [
            "## 5. Review",
            f"The reviewer (`{s['reviewer']}`) labelled a stratified random sample of {s['sample_size']} records blind (it never saw the assigned label).",
            f"- Agreement with assigned labels: **{s['agreement_rate'] * 100:.1f}%** (95% CI {s['agreement_ci95'][0] * 100:.1f}–{s['agreement_ci95'][1] * 100:.1f}%); {s['disputed_count']} disputes.",
            f"- Resolution: disputed records with a hand label were relabelled to it; otherwise the whole pair was dropped ({dropped} pairs). Every change is in `review_log.json`.",
        ]
        if hc.get("labelled"):
            lines += [f"- Hand-check set: {hc['labelled']} records labelled by the user; dataset-vs-human agreement "
                      f"**{hc['dataset_vs_human'] * 100:.1f}%**; reviewer-vs-human "
                      f"**{(hc['reviewer_vs_human'] or 0) * 100:.1f}%** on {hc['reviewer_labelled_overlap']} overlapping records."]
        else:
            lines += ["- **Hand-check set pending:** `handcheck_set.jsonl` has the ~100 records to label; run `sg data handcheck-label` "
                      "then `sg data split` to fold the agreement into this report."]
        lines += ["", "## 6. Splits", "",
                  "| Split | Records |", "|---|---|",
                  *[f"| {k} | {v:,} |" for k, v in sorted(eda["split_counts"].items())],
                  f"| test_decomposed (extra slice) | {eda['decomposed_slice']:,} |",
                  f"| heldout_harness (Module A trajectories) | {eda['heldout_harness']:,} |", "",
                  f"Pair-ID leakage across splits: **{eda['leaked_pairs_count']}**.", "",
                  "## 7. Limitations", "",
                  "- Labels are produced by a simulation rule, so the gate learns that rule; reviewer and hand-check agreement bound how well the rule matches human judgement.",
                  "- Scenes are text-only and the action space is the five scripted scenarios.",
                  "- The decomposed slice is template-generated (not Gemma) so each step is guaranteed innocuous on its own."]
        write_report(self.run_dir, "dataset_report", "\n".join(lines), "Module B — Dataset Report")

    # ------------------------------------------------------------------ orchestration
    def run_full_pipeline(self, target_pairs: Optional[int] = None, on_progress=None,
                          review_sample_size: Optional[int] = None) -> dict[str, Any]:
        self.state_mgr.check_prerequisites_for_stage("B_dataset")
        write_manifest(self.run_id, self.config, self.results_dir)
        self.state_mgr.set_stage_status("B_dataset", StageStatus.RUNNING)
        try:
            gen_stats = self.generate(target_pairs, on_progress=on_progress)
            self.review(review_sample_size)
            res = self.finalize()
        except Exception as e:
            self.state_mgr.set_stage_status("B_dataset", StageStatus.FAILED, error=f"{type(e).__name__}: {e}")
            raise
        self.state_mgr.set_stage_status(
            "B_dataset", StageStatus.PASSED if res["passed"] else StageStatus.FAILED,
            details={"pairs_count": res["pairs"], "reviewed": True, "reviewer_agreement": res["agreement_rate"],
                     "handcheck": res["handcheck"], "dataset_file": str(self.run_dir / "dataset.jsonl"),
                     "report_html": str(self.run_dir / "dataset_report.html")},
            error=None if res["passed"] else "pass check failed (pairs < target, leakage, or no review)")
        return {**res, "gen_stats": gen_stats}
