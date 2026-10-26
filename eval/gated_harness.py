"""Module D step 2: the Module A conditions re-run with every tool call passing through the gate."""
import csv
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from common.exceptions import ModelUnavailableError, StaleUngatedRunError
from common.guards import assert_real_policy_config
from common.state import PipelineStateManager
from common.stats import wilson_score_interval
from gate.inference import SafetyGateInference
from harness.engine import HarnessEngine
from harness.policies import OllamaPolicy
from harness.runner import (CONDITIONS, HarnessRunner, aggregate_trials, load_all_scenarios,
                            read_jsonl, trial_seed)

ROBOT_CONDITIONS = [(v, f) for v, f in CONDITIONS if f != "chat"]  # a chat reply has no tool executor to gate


class GateAdapter:
    """Plugs SafetyGateInference into HarnessEngine: evaluate_action -> (decision, probabilities)."""

    def __init__(self, inference: SafetyGateInference):
        self.inference = inference
        self.decisions: dict[str, int] = {"allow": 0, "block": 0, "escalate": 0}
        self.latencies: list[float] = []

    def evaluate_action(self, instruction, scene, history, proposed_action):
        r = self.inference.evaluate_proposed_action(instruction, scene, history, proposed_action)
        self.decisions[r["decision"]] += 1
        self.latencies.append(r["latency_sec"])
        return r["decision"], {"allow": round(r["p_allow"], 4), "block": round(r["p_block"], 4),
                               "escalate": round(r["p_escalate"], 4)}


def rate(k: int, n: int) -> str:
    p, lo, hi = wilson_score_interval(k, n)
    return f"{p * 100:.1f}% [{lo * 100:.1f}%, {hi * 100:.1f}%]"


class GatedHarnessRunner:
    def __init__(self, config: dict[str, Any], run_id: str, results_dir: Path | str = "results"):
        self.config, self.run_id = config, run_id
        self.results_dir = Path(results_dir)
        self.run_dir = self.results_dir / run_id
        self.state_mgr = PipelineStateManager(run_id=run_id, results_dir=results_dir)
        self.scenarios = load_all_scenarios()
        h = config["harness"]
        self.engine = HarnessEngine(h.get("max_tool_calls", 15), h.get("timeout_seconds", 300))
        self.ungated = HarnessRunner(config, self.results_dir, run_id)

    # -- the ungated run must match what we are about to compare against ---------------------------
    def check_ungated_fresh(self, rerun: Optional[bool]) -> Optional[str]:
        sig_file = self.run_dir / "ungated_signature.json"
        if not sig_file.exists() or not (self.run_dir / "trials.jsonl").exists():
            raise ModelUnavailableError("No ungated replication found for this run; Module A must run first.")
        old = json.loads(sig_file.read_text())
        digests = {}
        for m in assert_real_policy_config(self.config):
            p = OllamaPolicy(model_name=m["model_name"])
            digests[m["id"]] = p.check_available()["digest"]
        now = self.ungated.harness_signature(digests)
        if now == old:
            return None
        msg = ("The ungated replication is stale: harness config, scenarios or model digests changed since it ran "
               f"(then {old}, now {now}).")
        if rerun is None:
            raise StaleUngatedRunError(msg + " Re-run it (rerun_ungated=True) or compare anyway (False).")
        if rerun:
            stale = self.run_dir / f"trials_stale_{int(time.time())}.jsonl"
            (self.run_dir / "trials.jsonl").rename(stale)
            self.ungated.run_replication()
            return None
        return msg + " Proceeding on explicit request; the comparison is NOT like-for-like."

    def run_gated_replication(self, trials_per_condition: Optional[int] = None,
                              on_trial_callback: Optional[Callable[[dict[str, Any]], None]] = None,
                              rerun_ungated: Optional[bool] = None) -> dict[str, Any]:
        self.state_mgr.check_gated_harness_prerequisites()
        stale_note = self.check_ungated_fresh(rerun_ungated)
        h = self.config["harness"]
        n_trials = trials_per_condition or h.get("trials_per_condition", 20)
        seed_base = h.get("random_seed_base", 42)
        policies = {}
        for m in assert_real_policy_config(self.config):
            p = OllamaPolicy(model_name=m["model_name"], temperature=h.get("temperature", 0.2),
                             request_timeout=h.get("request_timeout_seconds", 300), think=m.get("think", False))
            p.check_available()
            policies[m["id"]] = p

        inference = SafetyGateInference(self.run_id, self.results_dir, self.config["gate_training"]["history_window_n"])
        gate = GateAdapter(inference)
        out_file = self.run_dir / "gated_trials.jsonl"
        done = {t["trial_id"] for t in read_jsonl(out_file)}
        conds = [(mid, sid, v, f) for mid in policies for sid in self.scenarios for v, f in ROBOT_CONDITIONS]
        total = len(conds) * n_trials
        completed, fresh, t0 = len(done), 0, time.time()
        with open(out_file, "a", encoding="utf-8") as fp:
            for mid, sid, variant, framing in conds:
                for i in range(n_trials):
                    seed = trial_seed(seed_base, i)
                    tid = f"{sid}_{variant}_{framing}_{mid}_{seed}_gated"
                    if tid in done:
                        continue
                    tr = self.engine.run_episode(self.scenarios[sid], variant, framing, policies[mid],
                                                 seed=seed, model_id=mid, gate=gate)
                    fp.write(json.dumps(tr) + "\n")
                    fp.flush()
                    completed += 1
                    fresh += 1
                    if on_trial_callback:
                        on_trial_callback({"completed": completed, "total": total, "current_trial": tr,
                                           "eta_sec": round((total - completed) * (time.time() - t0) / fresh, 1)})
        inference.close()
        gated = read_jsonl(out_file)
        if len(gated) < total:
            raise RuntimeError(f"Expected {total} gated trials, found {len(gated)}")
        summary = self._summarise(gated, read_jsonl(self.run_dir / "trials.jsonl"), gate, stale_note)
        return summary

    def _summarise(self, gated: list[dict[str, Any]], ungated: list[dict[str, Any]], gate: GateAdapter,
                   stale_note: Optional[str]) -> dict[str, Any]:
        ga, ua = aggregate_trials(gated), aggregate_trials([t for t in ungated if t["framing"] != "chat"])
        with open(self.run_dir / "gated_results_table.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["model_id", "scenario_id", "variant", "framing", "total_trials", "refused", "attempted", "completed",
                        "completed_rate", "completed_ci_lower", "completed_ci_upper"])
            for (m, s, v, fr), c in sorted(ga.items()):
                p, lo, hi = wilson_score_interval(c["completed-equivalent"], c["total"])
                w.writerow([m, s, v, fr, c["total"], c["refused"], c["attempted"], c["completed-equivalent"],
                            round(p, 4), round(lo, 4), round(hi, 4)])
        with open(self.run_dir / "with_vs_without_gate_comparison.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["model_id", "scenario_id", "variant", "framing", "ungated_n", "gated_n", "ungated_completed",
                        "gated_completed", "ungated_completed_rate", "gated_completed_rate", "ungated_refused_rate", "gated_refused_rate"])
            for key in sorted(ga):
                u, g = ua[key], ga[key]
                w.writerow([*key, u["total"], g["total"], u["completed-equivalent"], g["completed-equivalent"],
                            round(u["completed-equivalent"] / u["total"], 4), round(g["completed-equivalent"] / g["total"], 4),
                            round(u["refused"] / u["total"], 4), round(g["refused"] / g["total"], 4)])

        def pool(agg, variant):
            ks = [c for (m, s, v, fr), c in agg.items() if v == variant]
            return sum(c["completed-equivalent"] for c in ks), sum(c["total"] for c in ks)

        hu, hg = pool(ua, "harmful"), pool(ga, "harmful")
        bu, bg = pool(ua, "benign_twin"), pool(ga, "benign_twin")
        benign_trials = [t for t in gated if t["variant"] == "benign_twin"]
        benign_intervened = sum(1 for t in benign_trials if t["gate_interventions"])
        return {
            "stale_note": stale_note, "gate_decisions": gate.decisions,
            "gate_latency_p50_ms": round(sorted(gate.latencies)[len(gate.latencies) // 2] * 1000, 1) if gate.latencies else None,
            "harmful_completed_ungated": {"k": hu[0], "n": hu[1], "rate": rate(*hu)},
            "harmful_completed_gated": {"k": hg[0], "n": hg[1], "rate": rate(*hg)},
            "benign_completed_ungated": {"k": bu[0], "n": bu[1], "rate": rate(*bu)},
            "benign_completed_gated": {"k": bg[0], "n": bg[1], "rate": rate(*bg)},
            "benign_trials_with_any_intervention": {"k": benign_intervened, "n": len(benign_trials), "rate": rate(benign_intervened, len(benign_trials))},
            "total_gated_trials": len(gated),
        }
