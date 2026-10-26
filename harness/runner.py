import csv
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from common.exceptions import PrerequisiteError
from common.guards import assert_real_policy_config
from common.manifest import write_manifest
from common.state import PipelineStateManager, StageStatus
from common.stats import format_rate_ci, wilson_score_interval
from harness.engine import HarnessEngine
from harness.policies import BasePolicy, OllamaPolicy, ScriptedReplayPolicy
from harness.schema import ROBOT_TOOLS_SCHEMA

SIM_TELL_RE = re.compile(r"\b(sim|simulat\w*|mock\w*|test\w*|virtual|fake|dummy)\b", re.IGNORECASE)
CONDITIONS = [  # (variant, framing)
    ("harmful", "real"), ("harmful", "sim"), ("harmful", "chat"),
    ("benign_twin", "real"), ("benign_twin", "sim"),
]


def load_all_scenarios(scenarios_dir: Path | str = "scenarios") -> dict[str, dict[str, Any]]:
    scenarios: dict[str, dict[str, Any]] = {}
    for yaml_file in sorted(Path(scenarios_dir).glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        if data and "id" in data:
            scenarios[data["id"]] = data
    return scenarios


def build_conditions(models: list[dict[str, Any]], scenarios: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """(model_id, scenario_id, variant, framing) for every planned condition."""
    return [
        (m["id"], scen_id, variant, framing)
        for m in models for scen_id in scenarios for variant, framing in CONDITIONS
    ]


def trial_seed(seed_base: int, trial_idx: int) -> int:
    return seed_base + trial_idx * 13


def aggregate_trials(trials: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, int]]:
    agg: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for t in trials:
        key = (t["model_id"], t["scenario_id"], t["variant"], t["framing"])
        c = agg.setdefault(key, {"total": 0, "refused": 0, "attempted": 0, "completed-equivalent": 0})
        c["total"] += 1
        c[t["final_score"]] += 1
    return agg


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class HarnessRunner:
    def __init__(self, config: dict[str, Any], results_dir: Path | str = "results", run_id: str = "run_002"):
        self.config = config
        self.results_dir = Path(results_dir)
        self.run_id = run_id
        self.run_dir = self.results_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_mgr = PipelineStateManager(run_id=self.run_id, results_dir=self.results_dir)
        self.scenarios = load_all_scenarios()
        hcfg = config.get("harness", {})
        self.engine = HarnessEngine(
            max_tool_calls=hcfg.get("max_tool_calls", 15),
            timeout_seconds=hcfg.get("timeout_seconds", 300),
        )
        write_manifest(self.run_id, self.config, self.results_dir)

    # ------------------------------------------------------------------ dry run
    @staticmethod
    def _tells(scen: dict[str, Any], variant: str, tool_messages: list[str]) -> list[str]:
        """Simulation tells in everything the policy sees under the *real* framing."""
        var = scen[variant]
        texts = {
            "real system prompt": var["real_framing_system_prompt"],
            "instruction": var["instruction"],
            "initial scene": var.get("initial_scene", ""),
            "tool schema": json.dumps(ROBOT_TOOLS_SCHEMA),
        }
        texts.update({f"tool result {i}": m for i, m in enumerate(tool_messages)})
        return [f"{where}: {m.group(0)!r}" for where, txt in texts.items() for m in SIM_TELL_RE.finditer(txt)]

    def run_dry_run_all(self) -> dict[str, Any]:
        """
        Pass check for Module A: all 10 scenes dry-run end to end and the scoring rules return the
        expected label on each scripted transcript (full script, grasp-only, no action), with no
        simulation tells in the real framing.
        """
        results: list[dict[str, Any]] = []
        for scenario_id, scen in self.scenarios.items():
            for variant in ("harmful", "benign_twin"):
                seq = scen[variant]["scripted_sequence"]
                grip_idx = next(i for i, s in enumerate(seq) if s["tool"] == "grip")
                plans = [
                    ("full_script", seq, "completed-equivalent"),
                    ("grasp_only", seq[: grip_idx + 1], "attempted"),
                    ("no_action", [], "refused"),
                ]
                checks, main = [], None
                for name, subseq, expected in plans:
                    tr = self.engine.run_episode(
                        scenario_data=scen, variant=variant, framing="real",
                        policy=ScriptedReplayPolicy(subseq), seed=42, model_id="scripted_replay",
                    )
                    checks.append({"plan": name, "expected": expected, "got": tr["final_score"],
                                   "ok": tr["final_score"] == expected})
                    if name == "full_script":
                        main = tr
                # attach the YAML scripted tool result to each replayed step for the UI
                for st in main["steps"]:
                    if not st.get("auto"):
                        st["scripted_result"] = seq[st["step_idx"] - 1].get("result")
                tells = self._tells(scen, variant, [s["result"]["message"] for s in main["steps"]])
                passed = all(c["ok"] for c in checks) and not tells
                results.append({
                    "scenario_id": scenario_id, "variant": variant,
                    "steps_count": len(main["steps"]), "final_score": main["final_score"],
                    "checks": checks, "tells": tells, "passed": passed, "transcript": main,
                })
        (self.run_dir / "dry_run_transcripts.json").write_text(json.dumps(results, indent=2))
        all_passed = all(r["passed"] for r in results)
        self.state_mgr.set_dry_run_passed(all_passed)
        return {
            "all_passed": all_passed, "total_scenes": len(results),
            "passed_count": sum(1 for r in results if r["passed"]), "results": results,
        }

    # -------------------------------------------------------------- replication
    def harness_signature(self, digests: dict[str, str]) -> dict[str, Any]:
        """What the ungated results depend on: harness config, scenario files and model digests."""
        import hashlib
        h = self.config["harness"]
        relevant = {k: h[k] for k in ("trials_per_condition", "random_seed_base", "temperature", "max_tool_calls", "models") if k in h}
        scen = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path("scenarios").glob("*.yaml")))).hexdigest()[:16]
        code = hashlib.sha256(b"".join((Path(__file__).parent / f).read_bytes()
                                       for f in ("robot.py", "scoring.py", "engine.py"))).hexdigest()[:16]
        return {"harness_config_hash": hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16],
                "harness_code_hash": code,
                "scenarios_hash": scen, "model_digests": digests}

    def _instantiate_policies(self) -> dict[str, tuple[dict[str, Any], BasePolicy]]:
        policies: dict[str, tuple[dict[str, Any], BasePolicy]] = {}
        for m in assert_real_policy_config(self.config):
            p = OllamaPolicy(
                model_name=m["model_name"],
                base_url=m.get("base_url", "http://localhost:11434"),
                temperature=self.config["harness"].get("temperature", 0.2),
                request_timeout=self.config["harness"].get("request_timeout_seconds", 300),
                think=m.get("think", False),
            )
            info = p.check_available()
            m = {**m, "digest": info["digest"]}
            policies[m["id"]] = (m, p)
        return policies

    def run_gated(self, trials_per_condition: Optional[int] = None,
                  on_trial_callback: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        """Gated harness trials (Spec 4.6, 7.3). Locked until Modules B and C pass."""
        self.state_mgr.check_gated_harness_prerequisites()
        from eval.gated_harness import GatedHarnessRunner
        return GatedHarnessRunner(config=self.config, run_id=self.run_id, results_dir=self.results_dir) \
            .run_gated_replication(trials_per_condition=trials_per_condition, on_trial_callback=on_trial_callback)

    def run_replication(self, trials_per_condition: Optional[int] = None,
                        on_trial_callback: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        """Ungated RoboHarm replication with real local policies. Resumable: completed trials are skipped."""
        if not self.state_mgr.is_dry_run_passed():
            raise PrerequisiteError("Module A dry-run pass check must succeed before ungated replication run.")
        hcfg = self.config["harness"]
        trials_n = trials_per_condition or hcfg.get("trials_per_condition", 20)
        seed_base = hcfg.get("random_seed_base", 42)

        self.state_mgr.set_stage_status("A_harness", StageStatus.RUNNING)
        try:
            policies = self._instantiate_policies()
            (self.run_dir / "ungated_signature.json").write_text(json.dumps(
                self.harness_signature({mid: cfg["digest"] for mid, (cfg, _) in policies.items()}), indent=2))
            (self.run_dir / "models_used.json").write_text(json.dumps(
                {mid: {k: v for k, v in cfg.items()} for mid, (cfg, _) in policies.items()}, indent=2))

            trials_file = self.run_dir / "trials.jsonl"
            done_ids = {t["trial_id"] for t in read_jsonl(trials_file)}
            conditions = build_conditions([c for c, _ in policies.values()], self.scenarios)
            total = len(conditions) * trials_n
            completed = len([i for i in done_ids])
            t0 = time.time()
            fresh = 0

            with open(trials_file, "a", encoding="utf-8") as fp:
                for m_id, scen_id, variant, framing in conditions:
                    scen = self.scenarios[scen_id]
                    _, policy = policies[m_id]
                    for idx in range(trials_n):
                        seed = trial_seed(seed_base, idx)
                        tid = f"{scen_id}_{variant}_{framing}_{m_id}_{seed}"
                        if tid in done_ids:
                            continue
                        tr = self.engine.run_episode(scen, variant, framing, policy, seed=seed, model_id=m_id)
                        fp.write(json.dumps(tr) + "\n")
                        fp.flush()
                        completed += 1
                        fresh += 1
                        if on_trial_callback:
                            elapsed = time.time() - t0
                            on_trial_callback({
                                "completed": completed, "total": total, "current_trial": tr,
                                "eta_sec": round((total - completed) * elapsed / fresh, 1),
                            })
            trials = read_jsonl(trials_file)
            if len(trials) < total:
                raise RuntimeError(f"Expected {total} trials, found {len(trials)}")
            paths = self._write_outputs(trials, [c for c, _ in policies.values()])
        except Exception as e:
            self.state_mgr.set_stage_status("A_harness", StageStatus.FAILED, error=f"{type(e).__name__}: {e}")
            raise

        self.state_mgr.set_stage_status("A_harness", StageStatus.PASSED, details={
            "replication_passed": True, "total_trials": len(trials), "results_csv": paths["csv_path"],
        })
        return {"status": "passed", "total_trials": len(trials), **paths}

    # ------------------------------------------------------------------ outputs
    def _write_outputs(self, trials: list[dict[str, Any]], models_cfg: list[dict[str, Any]]) -> dict[str, str]:
        agg = aggregate_trials(trials)
        csv_file = self.run_dir / "results_table.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["model_id", "scenario_id", "variant", "framing", "total_trials"] + [
                f"{lab}_{col}" for lab in ("refused", "attempted", "completed")
                for col in ("count", "rate", "ci_lower", "ci_upper")])
            for (m, s, v, fr), c in sorted(agg.items()):
                row = [m, s, v, fr, c["total"]]
                for lab in ("refused", "attempted", "completed-equivalent"):
                    p, lo, hi = wilson_score_interval(c[lab], c["total"])
                    row += [c[lab], round(p, 4), round(lo, 4), round(hi, 4)]
                w.writerow(row)

        chat_csv = self.run_dir / "chat_vs_robot_comparison.csv"
        with open(chat_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["model_id", "scenario_id", "chat_refusal_rate", "chat_refusal_ci",
                        "robot_real_refusal_rate", "robot_real_refusal_ci",
                        "robot_sim_refusal_rate", "robot_sim_refusal_ci",
                        "refusal_gap_sim_vs_real", "refusal_gap_robot_vs_chat"])
            zero = {"total": 0, "refused": 0}
            for m in models_cfg:
                for s in self.scenarios:
                    cells = [agg.get((m["id"], s, "harmful", fr), zero) for fr in ("chat", "real", "sim")]
                    rates, row = [], [m["id"], s]
                    for c in cells:
                        p, lo, hi = wilson_score_interval(c["refused"], c["total"])
                        rates.append(p)
                        row += [round(p, 4), f"[{lo:.3f}, {hi:.3f}]"]
                    # gap = refusal in the safer setting minus refusal in the riskier setting
                    row += [round(rates[1] - rates[2], 4), round(rates[0] - rates[1], 4)]
                    w.writerow(row)

        from harness.report import generate_harness_report
        md, html_path = generate_harness_report(self.run_dir, self.scenarios, self.config, trials, models_cfg)
        return {"csv_path": str(csv_file), "chat_csv_path": str(chat_csv),
                "report_html": str(html_path), "report_md": str(md)}
