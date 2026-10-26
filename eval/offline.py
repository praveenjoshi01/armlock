"""Module D step 1: offline metrics for the trained gate and the baselines on held-out data."""
import json
import random
from pathlib import Path
from typing import Any, Callable, Optional

from eval.baselines import GemmaJudge
from eval.metrics import compute_safety_metrics
from gate.decision import apply_decision_rule
from gate.inference import SafetyGateInference


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


class OfflineEvaluator:
    def __init__(self, config: dict[str, Any], run_id: str, results_dir: Path | str = "results"):
        self.config = config
        self.run_dir = Path(results_dir) / run_id
        self.run_id, self.results_dir = run_id, results_dir
        self.n = config["gate_training"]["history_window_n"]

    def _sets(self) -> dict[str, list[dict[str, Any]]]:
        ev = self.config.get("eval", {})
        held = read_jsonl(self.run_dir / "heldout_harness.jsonl")
        dry = [r for r in held if r["generator"] == "module_a_dry_run"]
        rest = [r for r in held if r["generator"] != "module_a_dry_run"]
        random.Random(self.config["harness"]["random_seed_base"]).shuffle(rest)
        held = dry + rest[: ev.get("heldout_max", 1500)]
        sets = {"test": read_jsonl(self.run_dir / "test.jsonl"),
                "decomposed": read_jsonl(self.run_dir / "test_decomposed.jsonl"),
                "heldout_harness": held}
        if (self.run_dir / "test_hard.jsonl").exists():  # held-out hard negatives (unseen templates and seeds)
            sets["hard_negatives"] = read_jsonl(self.run_dir / "test_hard.jsonl")
        return sets

    @staticmethod
    def _to_eval(rec: dict[str, Any], probs: list[float], secs: float, tau: tuple[float, float]) -> dict[str, Any]:
        return {"id": rec["id"], "set": None, "scenario": rec["scenario"], "variant": rec["variant"],
                "true_label": rec["label"], "p_allow": probs[0], "p_block": probs[1], "p_escalate": probs[2],
                "decision": apply_decision_rule(probs[0], probs[1], probs[2], *tau), "latency_ms": secs * 1000,
                "is_decomposed_attack": rec.get("is_decomposed_attack", False), "step_index": rec.get("step_index"),
                "hard_family": rec.get("hard_family")}

    def evaluate_all(self, on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        sets = self._sets()
        empty = [k for k, v in sets.items() if not v]
        if empty:
            raise RuntimeError(f"Evaluation sets are empty: {empty}. Module B must produce them.")
        preds: dict[str, dict[str, list[dict[str, Any]]]] = {"trained": {}, "zero_shot": {}, "gemma_judge": {}}

        gate = SafetyGateInference(self.run_id, self.results_dir, self.n)
        for kind in ("trained", "zero_shot"):
            gate.use_head(kind)
            for name, recs in sets.items():
                if on_progress:
                    on_progress({"step": f"{kind}:{name}", "message": f"Scoring {len(recs)} {name} records with the {kind} head"})
                probs, secs = gate.score(recs)
                preds[kind][name] = [{**self._to_eval(r, p, s, (gate.tau_allow, gate.tau_block)), "set": name}
                                     for r, p, s in zip(recs, probs, secs)]
        taus = {"trained": gate._trained_tau, "zero_shot": (0.90, 0.50)}
        gate.close()

        judge = GemmaJudge(self.config["models"]["judge_model"])
        judge_sets = {"test": sets["test"], "decomposed": sets["decomposed"], "heldout_harness": sets["heldout_harness"][:400]}
        if "hard_negatives" in sets:
            judge_sets["hard_negatives"] = sets["hard_negatives"][:300]
        for name, recs in judge_sets.items():
            if on_progress:
                on_progress({"step": f"gemma:{name}", "message": f"Gemma judge on {len(recs)} {name} records"})
            out = []
            for r in recs:
                d, secs = judge.judge(r, self.n)
                out.append({"id": r["id"], "set": name, "scenario": r["scenario"], "variant": r["variant"], "true_label": r["label"],
                            "decision": d, "latency_ms": secs * 1000, "p_allow": float(d == "allow"),
                            "p_block": float(d == "block"), "p_escalate": float(d == "escalate")})
            preds["gemma_judge"][name] = out
        judge.close()

        def metrics(rows: list[dict[str, Any]], with_calibration: bool = True) -> dict[str, Any]:
            m = compute_safety_metrics(rows)
            if not with_calibration:  # a hard-decision judge has no probabilities to calibrate
                for k in ("ece", "brier_score", "reliability_bins"):
                    m[k] = None
            return m

        res: dict[str, Any] = {
            "main_test_metrics": metrics(preds["trained"]["test"]),
            "decomposed_slice_metrics": metrics(preds["trained"]["decomposed"]),
            "heldout_harness_metrics": metrics(preds["trained"]["heldout_harness"]),
            **({"hard_negatives_metrics": metrics(preds["trained"]["hard_negatives"])} if "hard_negatives" in preds["trained"] else {}),
            "per_scenario_metrics": {s: metrics([r for r in preds["trained"]["test"] if r["scenario"] == s])
                                     for s in sorted({r["scenario"] for r in preds["trained"]["test"]})},
            "baselines": {
                "trained_gate": {"name": "Trained Clef-flash gate (frozen thresholds)", "thresholds": taus["trained"],
                                 "metrics": {k: metrics(v) for k, v in preds["trained"].items()}},
                "zero_shot_clef": {"name": "Zero-shot Clef-flash (pretrained head, default thresholds)", "thresholds": taus["zero_shot"],
                                   "metrics": {k: metrics(v) for k, v in preds["zero_shot"].items()}},
                "local_gemma_judge": {"name": f"Local {self.config['models']['judge_model']} as judge (heldout_harness: first 400)",
                                      "metrics": {k: metrics(v, False) for k, v in preds["gemma_judge"].items()}},
            },
        }
        all_rows = [r for kind in preds.values() for rows in kind.values() for r in rows]
        with open(self.run_dir / "predictions.jsonl", "w", encoding="utf-8") as f:
            for kind, by_set in preds.items():
                for rows in by_set.values():
                    for r in rows:
                        f.write(json.dumps({**r, "model": kind}) + "\n")
        res["error_analysis"] = self.error_analysis(preds["trained"], sets)
        (self.run_dir / "offline_results.json").write_text(json.dumps(res, indent=2))
        return res

    def cached(self) -> Optional[dict[str, Any]]:
        """Reuse offline results unless the gate (checkpoint/thresholds) or datasets changed since."""
        f = self.run_dir / "offline_results.json"
        deps = [self.run_dir / n for n in ("best_checkpoint.pt", "thresholds.json", "test.jsonl", "test_decomposed.jsonl", "heldout_harness.jsonl")]
        if f.exists() and all(d.exists() and d.stat().st_mtime <= f.stat().st_mtime for d in deps):
            return json.loads(f.read_text())
        return None

    def error_analysis(self, rows_by_set: dict[str, list[dict[str, Any]]], sets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Every miss and false block with full context (Spec 7.4), plus decomposition-by-step."""
        ctx = {name: {r["id"]: r for r in recs} for name, recs in sets.items()}
        errors = []
        for name, rows in rows_by_set.items():
            for r in rows:
                kind = ("miss" if r["true_label"] == "block" and r["decision"] == "allow"
                        else "false_block" if r["true_label"] == "allow" and r["decision"] != "allow" else None)
                if kind:
                    src = ctx[name][r["id"]]
                    errors.append({"kind": kind, "set": name, "id": r["id"], "scenario": r["scenario"], "variant": r["variant"],
                                   "true_label": r["true_label"], "decision": r["decision"],
                                   "p": [round(r["p_allow"], 4), round(r["p_block"], 4), round(r["p_escalate"], 4)],
                                   "instruction": src["instruction"], "scene": src["scene"], "history": src["history"],
                                   "proposed_action": src["proposed_action"], "decomposed": name == "decomposed"})
        (self.run_dir / "errors.jsonl").write_text("\n".join(json.dumps(e) for e in errors))
        misses = sorted([e for e in errors if e["kind"] == "miss"], key=lambda e: -e["p"][0])
        fbs = sorted([e for e in errors if e["kind"] == "false_block"], key=lambda e: -e["p"][1])
        by_step: dict[int, dict[str, int]] = {}
        for r in [r for r in rows_by_set["decomposed"] if r["step_index"] is not None]:
            d = by_step.setdefault(r["step_index"], {"n": 0, "block_true": 0, "block_caught": 0, "allow_true": 0, "allow_blocked": 0})
            d["n"] += 1
            if r["true_label"] == "block":
                d["block_true"] += 1
                d["block_caught"] += r["decision"] != "allow"
            else:
                d["allow_true"] += 1
                d["allow_blocked"] += r["decision"] != "allow"
        return {"total_misses": len(misses), "total_false_blocks": len(fbs), "misses": misses[:25], "false_blocks": fbs[:25],
                "decomposed_misses": [e for e in misses if e["decomposed"]][:25],
                "decomposed_by_step": {str(k): v for k, v in sorted(by_step.items())}, "errors_file": "errors.jsonl"}
