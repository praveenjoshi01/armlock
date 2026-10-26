"""Side-by-side comparison of two runs' gates on the SAME evaluation records (the newer run's v2-labelled sets)."""
import json
from pathlib import Path
from collections import Counter
from typing import Any

from common.reporting import write_report
from common.stats import format_rate_ci
from eval.metrics import compute_safety_metrics
from eval.offline import OfflineEvaluator
from gate.decision import apply_decision_rule
from gate.inference import SafetyGateInference
from harness.runner import read_jsonl


def _rows(recs, probs, secs, tau):
    return [{"id": r["id"], "scenario": r["scenario"], "true_label": r["label"], "p_allow": p[0], "p_block": p[1],
             "p_escalate": p[2], "decision": apply_decision_rule(p[0], p[1], p[2], *tau), "latency_ms": s * 1000,
             "step_index": r.get("step_index")} for r, p, s in zip(recs, probs, secs)]


def score_base_gate_on(config: dict[str, Any], base: str, new: str, results_dir: Path) -> dict[str, Any]:
    out_file = results_dir / new / f"compare_{base}_gate_on_{new}_sets.json"
    if out_file.exists():
        return json.loads(out_file.read_text())
    sets = OfflineEvaluator(config, new, results_dir)._sets()
    gate = SafetyGateInference(base, results_dir, config["gate_training"]["history_window_n"])
    res, rows = {}, {}
    for name, recs in sets.items():
        probs, secs = gate.score(recs)
        rows[name] = _rows(recs, probs, secs, gate._trained_tau)
        res[name] = compute_safety_metrics(rows[name])
    res["rows"] = rows
    res["thresholds"] = list(gate._trained_tau)
    gate.close()
    out_file.write_text(json.dumps(res, indent=2))
    return res


def _physics_rows(b_dir: Path, base: str) -> list[str]:
    """If the base run was recorded without physics, add its physics re-score (replay) for context."""
    f = b_dir / "gated_trials_rescored_physics.jsonl"
    if not f.exists():
        return []
    rows = read_jsonl(f)
    h = [r for r in rows if r["variant"] == "harmful"]
    b = [r for r in rows if r["variant"] == "benign_twin"]
    c = lambda xs: format_rate_ci(sum(r["score_physics"] == "completed-equivalent" for r in xs), len(xs))
    return [f"| …`{base}` gate re-scored under physics (replay, {sum(r['diverged'] for r in rows)} diverged trials) | – | harmful {c(h)}, benign {c(b)} | – |"]


def compare(config: dict[str, Any], base: str, new: str, results_dir: Path = Path("results")) -> tuple[Path, Path]:
    b_dir, n_dir = results_dir / base, results_dir / new
    b_metrics, n_metrics = json.loads((b_dir / "metrics.json").read_text()), json.loads((n_dir / "metrics.json").read_text())
    base_on_new = score_base_gate_on(config, base, new, results_dir)
    n_sets = {"test": n_metrics["main_test_metrics"], "decomposed": n_metrics["decomposed_slice_metrics"],
              "heldout_harness": n_metrics["heldout_harness_metrics"]}
    if "hard_negatives_metrics" in n_metrics:
        n_sets["hard_negatives"] = n_metrics["hard_negatives_metrics"]
    tgt = config["thresholds"]
    pt = lambda m: f"{format_rate_ci(m['harmful_pass_through_count'], m['harmful_samples'])}"
    fb = lambda m: f"{format_rate_ci(m['benign_false_block_count'], m['benign_samples'])}"
    esc = lambda m: f"{m['escalate_rate'] * 100:.1f}%"

    def verdict(m):
        ok_pt = m["harmful_pass_through_rate"] < tgt["target_max_harmful_pass_through"]
        ok_fb = m["benign_false_block_rate"] < tgt["target_max_benign_false_blocks"]
        return f"{'PASS' if ok_pt else 'FAIL'} / {'PASS' if ok_fb else 'FAIL'}"

    def describe(run_dir: Path) -> tuple[str, str, str]:
        st = json.loads((run_dir / "state.json").read_text())["stages"]
        rules = Counter(r.get("label_rule", "v1") for r in read_jsonl(run_dir / "train.jsonl"))
        a = st["A_harness"].get("reused_from")
        return (st["B_dataset"].get("built_from", "original Module B pipeline"), rules.most_common(1)[0][0],
                f"reused from {a}" if a else "own run")

    b_desc, n_desc = describe(b_dir), describe(n_dir)
    shared_a = (b_dir / "trials.jsonl").read_bytes() == (n_dir / "trials.jsonl").read_bytes()
    md = [
        f"# Gate comparison: `{base}` vs `{new}`", "",
        f"- `{base}`: data = {b_desc[0]}; training labels mostly rules {b_desc[1]}; Module A {b_desc[2]}.",
        f"- `{new}`: data = {n_desc[0]}; training labels mostly rules {n_desc[1]}; Module A {n_desc[2]}.", "",
        f"Both gates are the same Clef-flash model (frozen Qwen3.5-9B backbone, fine-tuned decision head); only the training data differs. "
        f"Offline numbers below score **both gates on the same `{new}` evaluation records**, labelled with `{new}`'s rules, each gate using its own frozen thresholds "
        f"(`{base}`: τ_allow={base_on_new['thresholds'][0]}, τ_block={base_on_new['thresholds'][1]}; "
        f"`{new}`: τ_allow={n_metrics['baselines']['trained_gate']['thresholds'][0]}, τ_block={n_metrics['baselines']['trained_gate']['thresholds'][1]}).", "",
        "## 1. Offline, same records (95% Wilson CIs)", "",
        "| Set | N | Gate | Harmful pass-through | Benign false blocks | Escalate | Targets (pass-through / false blocks) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in n_sets:
        bm, nm = base_on_new[name], n_sets[name]
        md += [f"| {name} | {nm['total_samples']} | `{base}` | {pt(bm)} | {fb(bm)} | {esc(bm)} | {verdict(bm)} |",
               f"| | | `{new}` | **{pt(nm)}** | **{fb(nm)}** | {esc(nm)} | {verdict(nm)} |"]
    md += ["", "Calibration and latency (test set):", "",
           "| Gate | ECE | Brier | Latency p50 / p95 |", "|---|---|---|---|",
           f"| `{base}` | {base_on_new['test']['ece']} | {base_on_new['test']['brier_score']} | {base_on_new['test']['latency_p50_ms']:.0f} / {base_on_new['test']['latency_p95_ms']:.0f} ms |",
           f"| `{new}` | {n_sets['test']['ece']} | {n_sets['test']['brier_score']} | {n_sets['test']['latency_p50_ms']:.0f} / {n_sets['test']['latency_p95_ms']:.0f} ms |", "",
           "### Per scenario, heldout_harness (Module A trajectories)", "",
           "| Scenario | `" + base + "` pass-through | `" + new + "` pass-through | `" + base + "` false blocks | `" + new + "` false blocks |", "|---|---|---|---|---|"]
    new_preds = [p for p in read_jsonl(n_dir / "predictions.jsonl") if p["model"] == "trained" and p["set"] == "heldout_harness"]
    base_rows = base_on_new["rows"]["heldout_harness"]
    for scen in sorted({p["scenario"] for p in new_preds}):
        nm = compute_safety_metrics([p for p in new_preds if p["scenario"] == scen])
        bm = compute_safety_metrics([p for p in base_rows if p["scenario"] == scen])
        md.append(f"| `{scen}` | {pt(bm)} | {pt(nm)} | {fb(bm)} | {fb(nm)} |")
    md += ["",
           "## 2. As each run reported it (own labels; not directly comparable)", "",
           "| Run | Labels | Test pass-through | Test false blocks | Decomposed pass-through | Heldout pass-through |", "|---|---|---|---|---|---|"]
    for run, m, lab in ((base, b_metrics, b_desc[1]), (new, n_metrics, n_desc[1])):
        md.append(f"| `{run}` | {lab} | {pt(m['main_test_metrics'])} | {fb(m['main_test_metrics'])} | "
                  f"{pt(m['decomposed_slice_metrics'])} | {pt(m['heldout_harness_metrics'])} |")
    bh, nh = b_metrics["harness_comparison"], n_metrics["harness_comparison"]
    md += ["", "## 3. End to end: real Qwen policy with the gate in the loop", "",
           ("Same ungated trials, conditions and seeds for both; each run's gated trials use its own gate."
            if shared_a else f"Same conditions and seeds, but each run has its own ungated trials (`{new}` ran Module A fresh); "
            f"the 'without gate' column is `{new}`'s."), "",
           "| Measure | Without gate | `" + base + "` gate | `" + new + "` gate |", "|---|---|---|---|",
           f"| Harmful completed-equivalent | {nh['harmful_completed_ungated']['rate']} | {bh['harmful_completed_gated']['rate']} | **{nh['harmful_completed_gated']['rate']}** |",
           *_physics_rows(b_dir, base),
           f"| Benign twin completed-equivalent | {nh['benign_completed_ungated']['rate']} | {bh['benign_completed_gated']['rate']} | **{nh['benign_completed_gated']['rate']}** |",
           f"| Benign trials interrupted at least once | – | {bh['benign_trials_with_any_intervention']['rate']} | {nh['benign_trials_with_any_intervention']['rate']} |",
           f"| Gate decisions (allow / block / escalate) | – | {bh['gate_decisions']} | {nh['gate_decisions']} |", "",
           "Per scenario (harmful completed-equivalent with the gate):", "",
           "| Scenario | `" + base + "` | `" + new + "` |", "|---|---|---|"]
    bg, ng = read_jsonl(b_dir / "gated_trials.jsonl"), read_jsonl(n_dir / "gated_trials.jsonl")
    for scen in sorted({t["scenario_id"] for t in ng}):
        c = lambda ts: format_rate_ci(sum(t["final_score"] == "completed-equivalent" for t in ts), len(ts))
        md.append(f"| `{scen}` | {c([t for t in bg if t['scenario_id'] == scen and t['variant'] == 'harmful'])} | "
                  f"{c([t for t in ng if t['scenario_id'] == scen and t['variant'] == 'harmful'])} |")
    bt, nt = json.loads((b_dir / "training_metrics.json").read_text()), json.loads((n_dir / "training_metrics.json").read_text())
    be, ne = json.loads((b_dir / "dataset_eda.json").read_text()), json.loads((n_dir / "dataset_eda.json").read_text())
    md += ["", "## 4. Data and training", "",
           "| | `" + base + "` | `" + new + "` |", "|---|---|---|",
           f"| Training records | {bt['n_train']:,} | {nt['n_train']:,} |",
           f"| Labels (allow / block / escalate, all splits) | {be['label_distribution'].get('allow', 0)} / {be['label_distribution'].get('block', 0)} / {be['label_distribution'].get('escalate', 0)} | "
           f"{ne['label_distribution'].get('allow', 0)} / {ne['label_distribution'].get('block', 0)} / {ne['label_distribution'].get('escalate', 0)} |",
           f"| Best epoch / frozen τ_allow, τ_block | {bt['best_epoch']} / {bt['threshold_tuning']['tau_allow']}, {bt['threshold_tuning']['tau_block']} | "
           f"{nt['best_epoch']} / {nt['threshold_tuning']['tau_allow']}, {nt['threshold_tuning']['tau_block']} |",
           f"| Validation targets met | {bt['threshold_tuning']['meets_targets']} | {nt['threshold_tuning']['meets_targets']} |", "",
           "## 5. Caveats", "",
           f"- Offline labels are `{new}`'s rules ({n_desc[1]}); where `{base}` was trained on different rules, part of its gap is the label change itself.",
           (f"- Module A trials are shared, so `heldout_harness` comes from the same ungated episodes for both runs." if shared_a else
            f"- `{new}` re-ran Module A on its own harness, so its gated trials are not paired episode-for-episode with `{base}`'s; offline numbers are on identical records."),
           "- The hand-check set is still unlabelled; no number here has been checked against human labels."]
    out = write_report(n_dir, "comparison_report", "\n".join(md), f"Gate Comparison {base} vs {new}")
    return out
