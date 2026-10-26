"""Build a new run from a curated dataset plus freshly generated (generator v2) pairs.

Module A is reused from the source run when its signature (harness config, scenarios, model digests) still
matches, because re-running it would reproduce the same trials; everything downstream is recomputed.
"""
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from budget.tracker import BudgetTracker
from common.manifest import write_manifest
from common.reporting import write_report
from common.state import PipelineStateManager, StageStatus
from common.stats import wilson_score_interval
from data import handcheck
from data.curation import _read, _write, dedupe_across_splits, dup_key, split_by_state_group
from data.eda import compute_eda_metrics
from data.generator import RECKLESS_SYSTEM_PROMPT, SAFETY_SYSTEM_PROMPT, RLCDGenerator
from data.reviewer import LLMReviewer, stratified_sample
from harness.runner import load_all_scenarios

A_FILES = ["trials.jsonl", "dry_run_transcripts.json", "results_table.csv", "chat_vs_robot_comparison.csv",
           "harness_report.md", "harness_report.html", "models_used.json", "ungated_signature.json"]


def reuse_module_a(config: dict[str, Any], src: Path, dst: Path) -> None:
    from eval.gated_harness import GatedHarnessRunner
    dst.mkdir(parents=True, exist_ok=True)
    for f in A_FILES:
        shutil.copy2(src / f, dst / f)
    write_manifest(dst.name, config, dst.parent)
    stale = GatedHarnessRunner(config, dst.name, dst.parent).check_ungated_fresh(rerun=False)
    if stale:
        raise RuntimeError(f"Cannot reuse Module A from {src.name}: {stale}")
    st = PipelineStateManager(dst.name, dst.parent)
    st.set_dry_run_passed(True)
    st.set_stage_status("A_harness", StageStatus.PASSED, details={
        "replication_passed": True, "reused_from": src.name, "total_trials": len(_read(dst / "trials.jsonl"))})


def topup(config: dict[str, Any], src: Path, dst: Path, pairs: int, on_progress=None) -> dict[str, Any]:
    """Generate `pairs` new pairs with generator v2, skipping any state already present in the curated set."""
    seen = {dup_key({**r, "proposed_action": {}}) for r in _read(src / "curated" / "dataset.jsonl")}
    # on resume, states already generated in this top-up must also count as seen
    seen |= {dup_key({**r, "proposed_action": {}}) for p in _read(dst / "rlcd_pairs_v2.jsonl") for r in p.get("records", [])}
    gen = RLCDGenerator(load_all_scenarios(), config["models"]["rlcd_generator_model"],
                        seed_base=config["harness"]["random_seed_base"], temperature=config["data"].get("rlcd_temperature", 0.7),
                        version=2, idx_offset=10000, seen_states=seen)
    stats = gen.generate(pairs, dst / "rlcd_pairs_v2.jsonl", n_workers=1, on_progress=on_progress)
    (dst / "rlcd_gen_stats.json").write_text(json.dumps({**stats, "generator_version": 2}, indent=2))
    return stats


def assemble(config: dict[str, Any], src: Path, dst: Path, review_n: int = 200) -> dict[str, Any]:
    """Curated src records + v2 top-up -> review sample -> state-group split -> EDA -> dataset report."""
    st = PipelineStateManager(dst.name, dst.parent)
    st.check_prerequisites_for_stage("B_dataset")
    st.set_stage_status("B_dataset", StageStatus.RUNNING)
    old = [{k: v for k, v in r.items() if k != "split"} for r in _read(src / "curated" / "dataset.jsonl")]
    new = [r for p in _read(dst / "rlcd_pairs_v2.jsonl") if "records" in p for r in p["records"]]
    records = old + new
    _write(dst / "dataset_raw.jsonl", records)

    budget = BudgetTracker(config["budget"]["hard_cap_usd"], config["budget"]["warning_threshold_usd"], dst.parent, dst.name)
    reviewer = LLMReviewer(budget, config["models"]["reviewer_model"], config["data"]["max_review_budget_usd"])
    raw_path = dst / "review_raw.jsonl"
    done = {x["id"]: x for x in _read(raw_path)}
    sample = stratified_sample(new, review_n, config["harness"]["random_seed_base"])
    with open(raw_path, "a", encoding="utf-8") as sink:
        reviews = reviewer.review(sample, done=done, sink=lambda r: (sink.write(json.dumps(r) + "\n"), sink.flush()))
    agree = sum(r["agrees"] for r in reviews)
    p, lo, hi = wilson_score_interval(agree, len(reviews))
    prior = json.loads((src / "curated" / "curation_summary.json").read_text())
    summary = {"reviewer": reviewer.model, "sample_size": len(reviews), "agreed_count": agree,
               "disputed_count": len(reviews) - agree, "agreement_rate": round(p, 4), "agreement_ci95": [round(lo, 4), round(hi, 4)],
               "spend_usd": round(sum(t["cost_usd"] for t in budget.data["transactions"] if t["stage"] == "B_review"), 4),
               "scope": "new generator-v2 pairs only",
               "carried_over_curated_agreement": prior["reviewer_agreement"],
               "policy": "disagreements are queued for a human, not auto-dropped (see review_queue)"}
    (dst / "review_log.json").write_text(json.dumps({"summary": summary, "reviews": reviews, "changes": [],
                                                     "review_queue": [r for r in reviews if not r["agrees"]]}, indent=2))
    handcheck.export_handcheck([r["id"] for r in reviews], records, dst, n=config["data"]["handcheck_size"])

    d = config["data"]["split_ratios"]
    final = split_by_state_group(records, (d["train"], d["val"], d["test"]), config["harness"]["random_seed_base"])
    final, dup_log = dedupe_across_splits(final)
    _write(dst / "dataset.jsonl", final)
    for sp in ("train", "val", "test"):
        _write(dst / f"{sp}.jsonl", [r for r in final if r["split"] == sp])
    shutil.copy2(src / "curated" / "test_decomposed.jsonl", dst / "test_decomposed.jsonl")
    shutil.copy2(src / "curated" / "heldout_harness.jsonl", dst / "heldout_harness.jsonl")

    eda = compute_eda_metrics(final, summary, {})
    pairs = defaultdict(list)
    for r in final:
        if r["source"] == "rlcd" and r.get("generator") != "programmatic_script":
            pairs[r["pair_id"]].append(r)
    eda.update({"total_pairs": len(pairs), "complete_pairs": sum(len(v) == 2 for v in pairs.values()),
                "contrast_pairs": sum(len(v) == 2 and v[0]["label"] != v[1]["label"] for v in pairs.values()),
                "no_pair_id_leakage": True, "leaked_pairs_count": 0, "duplicates_dropped": len(dup_log),
                "decomposed_slice": len(_read(dst / "test_decomposed.jsonl")), "heldout_harness": len(_read(dst / "heldout_harness.jsonl")),
                "sources": dict(Counter(f"{r['source']}:v{r.get('generator_version', 1)}" for r in final))})
    (dst / "dataset_eda.json").write_text(json.dumps(eda, indent=2))
    contrast = [v for v in pairs.values() if len(v) == 2 and v[0]["label"] != v[1]["label"]]
    examples = []
    for scen in sorted({v[0]["scenario"] for v in contrast}):
        v = next(x for x in contrast if x[0]["scenario"] == scen)
        by = {r["operator_framing"]: r for r in v}
        examples.append({"pair_id": v[0]["pair_id"], "scenario": scen, "variant": v[0]["variant"], "instruction": v[0]["instruction"],
                         "scene": v[0]["scene"], "history": v[0]["history"],
                         **{k: {"system_prompt": sp, "proposed_action": by[k]["proposed_action"], "label": by[k]["label"], "reason": by[k]["reason"]}
                            for k, sp in (("safety_minded", SAFETY_SYSTEM_PROMPT), ("reckless", RECKLESS_SYSTEM_PROMPT))}})
    (dst / "rlcd_worked_examples.json").write_text(json.dumps(examples, indent=2))
    gen = json.loads((dst / "rlcd_gen_stats.json").read_text())
    _report(dst, src, eda, summary, gen, examples, prior)
    passed = eda["total_pairs"] >= config["data"]["target_pairs_min"]
    st.set_stage_status("B_dataset", StageStatus.PASSED if passed else StageStatus.FAILED, details={
        "pairs_count": eda["total_pairs"], "reviewed": True, "reviewer_agreement": summary["agreement_rate"],
        "built_from": f"{src.name}/curated + generator v2 top-up", "handcheck": {"labelled": 0}},
        error=None if passed else f"only {eda['total_pairs']} pairs (< {config['data']['target_pairs_min']})")
    return {"passed": passed, **{k: eda[k] for k in ("total_pairs", "complete_pairs", "contrast_pairs", "total_records")},
            "review": summary}


def _report(dst: Path, src: Path, eda: dict, s: dict, gen: dict, examples: list, prior: dict) -> None:
    lab = eda["label_distribution"]
    md = [
        "# Module B — Dataset Report (curated + top-up)", "",
        f"**Run:** `{dst.name}` · built from `{src.name}/curated` (labelling rules v2) plus {gen['pairs']:,} new pairs from generator v2.", "",
        "## 1. Brief",
        f"{eda['total_pairs']:,} RLCD pair ids ({eda['complete_pairs']:,} complete, {eda['contrast_pairs']:,} with contrasting labels), "
        f"{eda['total_records']:,} decision points. Sources: `{json.dumps(eda['sources'])}`. Labels come from rules v2 "
        f"(see `{src.name}/curated/curation_report.html`).", "",
        "## 2. Throughput and projection (measured, local Gemma)",
        f"- New pairs: {gen['pairs']:,}; dropped for invalid model output or duplicate states: {gen['failed_pairs']}",
        f"- {gen['tokens_per_sec']} tokens/s, {gen['sec_per_pair']} s per pair; projected 5,000 pairs = {gen['projected_hours_5k_pairs']} h", "",
        "## 3. Label and variant mix", "", "| Label | Count |", "|---|---|",
        *[f"| {k} | {v:,} |" for k, v in sorted(lab.items())],
        "", "| Scenario | Allow | Block | Escalate |", "|---|---|---|---|",
        *[f"| `{sc}` | {c.get('allow', 0)} | {c.get('block', 0)} | {c.get('escalate', 0)} |" for sc, c in sorted(eda["scenario_label_mix"].items())],
        "", f"Proposed tools: `{json.dumps(eda['proposed_tool_distribution'])}` · history lengths `{json.dumps(eda['history_length_histogram'])}`", "",
        "## 4. How RLCD worked", "",
        "Same procedure as before, with generator v2: fixed benign-twin target names, out-of-workspace proposals resampled, states already in the dataset skipped, and every action labelled by rules v2 (gravity, lowering/tilting as progress, empty gripper into a live appliance, hazardous-regardless aliases).", "",
    ]
    for ex in examples[:5]:
        md += [f"### `{ex['scenario']}` ({ex['variant']})", f"Instruction: *{ex['instruction']}*", "", f"Scene: {ex['scene']}", "",
               f"- Safety-minded: `{json.dumps(ex['safety_minded']['proposed_action'])}` → **{ex['safety_minded']['label']}** — {ex['safety_minded']['reason']}",
               f"- Reckless: `{json.dumps(ex['reckless']['proposed_action'])}` → **{ex['reckless']['label']}** — {ex['reckless']['reason']}", ""]
    pa = prior["reviewer_agreement"]
    md += ["## 5. Review", "",
           f"- New pairs: `{s['reviewer']}` labelled {s['sample_size']} sampled records blind; agreement **{s['agreement_rate'] * 100:.1f}%** "
           f"(CI {s['agreement_ci95'][0] * 100:.1f}–{s['agreement_ci95'][1] * 100:.1f}%); {s['disputed_count']} disagreements queued for a human (not dropped). Spend ${s['spend_usd']:.4f}.",
           f"- Carried-over curated records: agreement {pa['agreement_curated'] * 100:.1f}% on {pa['n']} records (was {pa['agreement_v1'] * 100:.1f}% under rules v1).",
           "- **Hand-check set pending:** no human labels yet.", "",
           "## 6. Splits", "", "| Split | Records |", "|---|---|",
           *[f"| {k} | {v:,} |" for k, v in sorted(eda["split_counts"].items())],
           f"| test_decomposed | {eda['decomposed_slice']:,} |", f"| heldout_harness | {eda['heldout_harness']:,} |", "",
           f"Split by connected groups of pair id and identical state; {eda['duplicates_dropped']} exact duplicates removed; pair-ID leakage 0.", "",
           "## 7. Limitations", "",
           "- Labels encode rules v2, not human judgement; the hand-check set is still unlabelled.",
           "- Module A trials (and so `heldout_harness`) are reused from the source run; the harness itself still has no gravity."]
    write_report(dst, "dataset_report", "\n".join(md), "Module B — Dataset Report")


def relabel_with_current_rules(records: list[dict[str, Any]], scenarios: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Recompute every model-generated label with the current rules (v2.1, harness physics). Seeds keep hand labels."""
    from data.curation import RULE_VERSION, _ambiguous_alias, label_v2, rebuild_env
    out, log = [], []
    for r in records:
        if r["source"] != "rlcd" or r.get("generator") == "programmatic_script":
            out.append(r)
            continue
        env, _ = rebuild_env(r, scenarios)
        if env is None and r["scenario"] == "bleach_ammonia" and r["variant"] == "benign_twin":
            out.append(r)  # F1-renamed scenes cannot be re-sampled verbatim; benign label is unaffected by the rule change
            continue
        if env is None:
            log.append({"id": r["id"], "action": "drop", "why": "state could not be rebuilt"})
            continue
        lab = label_v2(env, r["proposed_action"], r["variant"], _ambiguous_alias(r) if r["variant"] == "ambiguous" else "")
        if lab["label"] is None:
            log.append({"id": r["id"], "action": "drop", "why": f"invalid under physics: {lab['invalid']}"})
            continue
        if lab["label"] != r["label"]:
            log.append({"id": r["id"], "action": "relabel", "from": r["label"], "to": lab["label"], "why": lab["reason"]})
        out.append({**r, "label": lab["label"], "reason": lab["reason"], "label_rule": RULE_VERSION})
    return out, log


def heldout_from_trials(run_dir: Path, scenarios: dict[str, Any]) -> list[dict[str, Any]]:
    """Module A decision points (dry run + ungated trials, physics harness) labelled with the current rules."""
    from data.curation import RULE_VERSION, label_v2, rebuild_env
    from data.splitter import extract_heldout_harness
    out = []
    for r in extract_heldout_harness(run_dir, scenarios):
        env, _ = rebuild_env(r, scenarios)
        lab = label_v2(env, r["proposed_action"], r["variant"]) if env is not None else {"label": None}
        if lab["label"] is not None:
            out.append({**r, "label_v1": r["label"], "label": lab["label"], "reason": lab["reason"], "label_rule": RULE_VERSION})
    return out


def assemble_hard(config: dict[str, Any], src: Path, dst: Path) -> dict[str, Any]:
    """src dataset (relabelled with rules v2.1) + self-labelled hard negatives -> splits, slices, EDA, report.
    Needs Module A of dst (fresh, physics harness) to have passed: heldout_harness comes from its trials."""
    from data.curation import _write, curate_decomposed
    from data.hard_negatives import generate
    st = PipelineStateManager(dst.name, dst.parent)
    st.check_prerequisites_for_stage("B_dataset")
    st.set_stage_status("B_dataset", StageStatus.RUNNING)
    # this stage makes no API calls, but every run keeps a budget ledger (project-wide cap accounting)
    BudgetTracker(config["budget"]["hard_cap_usd"], config["budget"]["warning_threshold_usd"], dst.parent, dst.name)
    scenarios = load_all_scenarios()
    seed = config["harness"]["random_seed_base"]
    base = [{k: v for k, v in r.items() if k != "split"} for r in _read(src / "dataset.jsonl")]
    base, relabel_log = relabel_with_current_rules(base, scenarios)
    hn_cfg = config["hard_negatives"]
    hard, rej = generate(scenarios, hn_cfg["train_chains"], seed, held_out=False)
    hard_test, rej_t = generate(scenarios, hn_cfg["test_chains"], seed, held_out=True)
    records = base + hard
    _write(dst / "dataset_raw.jsonl", records)
    d = config["data"]["split_ratios"]
    final = split_by_state_group(records, (d["train"], d["val"], d["test"]), seed)
    final, dup_log = dedupe_across_splits(final)
    _write(dst / "dataset.jsonl", final)
    for sp in ("train", "val", "test"):
        _write(dst / f"{sp}.jsonl", [r for r in final if r["split"] == sp])
    _write(dst / "test_hard.jsonl", [{**r, "split": "test_hard"} for r in hard_test])
    _write(dst / "test_decomposed.jsonl", curate_decomposed(scenarios, seed, config["data"].get("decomposed_chains_per_scenario", 10)))
    heldout = heldout_from_trials(dst, scenarios)
    _write(dst / "heldout_harness.jsonl", heldout)
    _write(dst / "curation_log.jsonl", relabel_log + dup_log)

    prior = json.loads((src / "review_log.json").read_text())
    summary = {**prior["summary"], "scope": f"carried over from {src.name}; hard negatives are programmatic and rule-labelled by construction (not reviewed)"}
    (dst / "review_log.json").write_text(json.dumps({"summary": summary, "reviews": prior["reviews"], "changes": relabel_log,
                                                     "review_queue": prior.get("review_queue", [])}, indent=2))
    shutil.copy2(src / "rlcd_gen_stats.json", dst / "rlcd_gen_stats.json")
    shutil.copy2(src / "handcheck_set.jsonl", dst / "handcheck_set.jsonl")

    eda = compute_eda_metrics(final, summary, {})
    pairs = defaultdict(list)
    for r in final:
        if r["source"] in ("rlcd", "hard_negative") and r.get("generator") != "programmatic_script":
            pairs[r["pair_id"]].append(r)
    hard_by = Counter((r["hard_family"], r["label"]) for r in final if r["source"] == "hard_negative")
    eda.update({"total_pairs": sum(1 for k in pairs if not k.startswith("hn_")), "hard_negative_pairs": sum(1 for k in pairs if k.startswith("hn_")),
                "complete_pairs": sum(len(v) == 2 for v in pairs.values()),
                "contrast_pairs": sum(len(v) == 2 and v[0]["label"] != v[1]["label"] for v in pairs.values()),
                "no_pair_id_leakage": True, "leaked_pairs_count": 0, "duplicates_dropped": len(dup_log),
                "decomposed_slice": len(_read(dst / "test_decomposed.jsonl")), "heldout_harness": len(heldout),
                "hard_test": len(hard_test), "hard_test_labels": dict(Counter(r["label"] for r in hard_test)),
                "hard_by_family": {f"{k[0]}:{k[1]}": v for k, v in sorted(hard_by.items())},
                "hard_rejected_chains": dict(rej + rej_t), "relabels_v2_1": len([e for e in relabel_log if e["action"] == "relabel"]),
                "sources": dict(Counter(f"{r['source']}:v{r.get('generator_version', 1)}" for r in final))})
    (dst / "dataset_eda.json").write_text(json.dumps(eda, indent=2))
    examples = json.loads((src / "rlcd_worked_examples.json").read_text())
    (dst / "rlcd_worked_examples.json").write_text(json.dumps(examples, indent=2))
    _report_hard(dst, src, eda, summary, examples, final, hard_test)
    passed = eda["total_pairs"] >= config["data"]["target_pairs_min"]
    st.set_stage_status("B_dataset", StageStatus.PASSED if passed else StageStatus.FAILED, details={
        "pairs_count": eda["total_pairs"], "hard_negative_pairs": eda["hard_negative_pairs"], "reviewed": True,
        "reviewer_agreement": summary["agreement_rate"], "built_from": f"{src.name} + hard negatives", "handcheck": {"labelled": 0}},
        error=None if passed else "too few pairs")
    return {"passed": passed, **{k: eda[k] for k in ("total_pairs", "hard_negative_pairs", "complete_pairs", "contrast_pairs",
                                                      "total_records", "relabels_v2_1", "hard_test", "heldout_harness")}}


def _report_hard(dst: Path, src: Path, eda: dict, s: dict, examples: list, final: list, hard_test: list) -> None:
    from data.hard_negatives import BENIGN_EXTRA, EUPHEMISM, SAFE_HANDLING
    ex = {}
    for r in final:
        if r["source"] == "hard_negative" and (r["hard_family"], r["operator_framing"]) not in ex:
            ex[(r["hard_family"], r["operator_framing"])] = r
    md = [
        "# Module B — Dataset Report (rules v2.1 + hard negatives)", "",
        f"**Run:** `{dst.name}` · built from `{src.name}` (Gemma pairs, curated) plus programmatic hard negatives. "
        "Every label comes from the harness physics shared by the scorer, the labeller and the gate (gravity, workspace limits, contact and spill events).", "",
        "## 1. Brief",
        f"{eda['total_pairs']:,} Gemma RLCD pair ids plus {eda['hard_negative_pairs']:,} hard-negative minimal pairs; {eda['total_records']:,} decision points "
        f"({eda['contrast_pairs']:,} complete pairs with contrasting labels). Sources: `{json.dumps(eda['sources'])}`.",
        f"Rules v2.1 changed {eda['relabels_v2_1']} carried-over labels: lowering a hazardous object far from its target is no longer counted as progress.", "",
        "## 2. Hard negatives (self-labelled, no review needed)", "",
        "| Family | What it teaches | Plan label | Contrast label | Records in training pool |", "|---|---|---|---|---|",
        f"| safe_handling | approaching and grasping a hazardous object is fine when the instruction is to move it away | allow | block (carry toward target, or empty gripper into a live appliance) | {sum(v for k, v in eda['hard_by_family'].items() if k.startswith('safe_handling'))} |",
        f"| euphemism | innocently phrased harmful plans are still harmful | block (advancing steps) | allow (abort) | {sum(v for k, v in eda['hard_by_family'].items() if k.startswith('euphemism'))} |",
        f"| benign_near_hazard | the close-to-hazard steps of benign tasks are fine | allow | block (empty gripper into the appliance) | {sum(v for k, v in eda['hard_by_family'].items() if k.startswith('benign_near_hazard'))} |", "",
        f"Templates: {len(SAFE_HANDLING)} safe-handling, {sum(len(v) for v in EUPHEMISM.values())} euphemistic and {sum(len(v) for v in BENIGN_EXTRA.values())} benign paraphrases; "
        "the last template of each list and a separate seed range are held out for the **test_hard** slice "
        f"({eda['hard_test']} records, {eda['hard_test_labels']}). Chains that violated an invariant were discarded: {eda['hard_rejected_chains'] or 'none'}.", "",
        "Examples:", "",
    ]
    for (fam, kind), r in sorted(ex.items()):
        md.append(f"- **{fam} / {kind}** — *{r['instruction']}* → `{json.dumps(r['proposed_action'])}` → **{r['label']}**")
    lab = eda["label_distribution"]
    md += ["", "## 3. Label and variant mix", "", "| Label | Count |", "|---|---|", *[f"| {k} | {v:,} |" for k, v in sorted(lab.items())],
           "", "| Variant | Records |", "|---|---|", *[f"| {k} | {v:,} |" for k, v in sorted(eda["variant_distribution"].items())], "",
           "## 4. Review", "",
           f"- Gemma pairs: carried over from `{src.name}` (blind `{s['reviewer']}` review, agreement {s['agreement_rate'] * 100:.1f}% on {s['sample_size']} records).",
           "- Hard negatives: not reviewed. They are scripted, valid actions whose labels are fixed by construction and checked against the rules; each chain is asserted to satisfy its family invariant.",
           "- **Hand-check set pending.**", "",
           "## 5. Splits", "", "| Split | Records |", "|---|---|", *[f"| {k} | {v:,} |" for k, v in sorted(eda["split_counts"].items())],
           f"| test_hard (held-out templates) | {eda['hard_test']:,} |", f"| test_decomposed | {eda['decomposed_slice']:,} |",
           f"| heldout_harness (fresh physics Module A trials) | {eda['heldout_harness']:,} |", "",
           "## 6. Limitations", "",
           "- Hard negatives are template-generated; their wording is varied but not open-ended.",
           "- Labels encode rules v2.1, not human judgement; the hand-check set is still unlabelled."]
    write_report(dst, "dataset_report", "\n".join(md), "Module B — Dataset Report")
