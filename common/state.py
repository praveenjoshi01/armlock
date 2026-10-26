import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from common.exceptions import PrerequisiteError


class StageStatus(str, Enum):
    LOCKED = "locked"
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"


STAGE_ORDER = [
    "A_harness",
    "B_dataset",
    "C_training",
    "D_evaluation",
    "final_report"
]

STAGE_NAMES = {
    "A_harness": "Module A — Harness (Replication)",
    "B_dataset": "Module B — Dataset Generation & Review",
    "C_training": "Module C — Gate Model Training",
    "D_evaluation": "Module D — Evaluation & End-to-End",
    "final_report": "Final Comprehensive Report"
}


class PipelineStateManager:
    def __init__(self, run_id: str, results_dir: Path | str = "results"):
        self.run_id = run_id
        self.results_dir = Path(results_dir)
        self.run_dir = self.results_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.run_dir / "state.json"
        self._load_or_init()

    def _default_state(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stages": {
                "A_harness": {
                    "status": StageStatus.READY.value,
                    "dry_run_passed": False,
                    "replication_passed": False,
                    "last_run": None,
                    "error": None
                },
                "B_dataset": {
                    "status": StageStatus.LOCKED.value,
                    "pairs_count": 0,
                    "reviewed": False,
                    "last_run": None,
                    "error": None
                },
                "C_training": {
                    "status": StageStatus.LOCKED.value,
                    "path_selected": None,
                    "best_checkpoint": None,
                    "last_run": None,
                    "error": None
                },
                "D_evaluation": {
                    "status": StageStatus.LOCKED.value,
                    "gated_replication_passed": False,
                    "last_run": None,
                    "error": None
                },
                "final_report": {
                    "status": StageStatus.LOCKED.value,
                    "generated_at": None,
                    "error": None
                }
            },
            "active_stage": "A_harness"
        }

    def _load_or_init(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.state = data
                return
            except Exception:
                pass
        self.state = self._default_state()
        self._save()

    def _save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def get_stage_status(self, stage_id: str) -> str:
        return self.state.get("stages", {}).get(stage_id, {}).get("status", StageStatus.LOCKED.value)

    def is_dry_run_passed(self) -> bool:
        return bool(self.state["stages"]["A_harness"].get("dry_run_passed", False))

    def set_dry_run_passed(self, passed: bool = True) -> None:
        self.state["stages"]["A_harness"]["dry_run_passed"] = passed
        self._save()

    def check_prerequisites_for_stage(self, stage_id: str) -> None:
        if stage_id not in STAGE_ORDER:
            raise ValueError(f"Unknown stage: {stage_id}")

        idx = STAGE_ORDER.index(stage_id)
        missing: list[str] = []
        for prior_stage in STAGE_ORDER[:idx]:
            prior_status = self.get_stage_status(prior_stage)
            if prior_status != StageStatus.PASSED.value:
                missing.append(prior_stage)

        if missing:
            names = [STAGE_NAMES.get(s, s) for s in missing]
            raise PrerequisiteError(
                f"Cannot execute {STAGE_NAMES.get(stage_id, stage_id)}. "
                f"Missing prerequisite stage(s) must pass first: {', '.join(names)}",
                missing_stages=missing
            )

    def check_gated_harness_prerequisites(self) -> None:
        """Section 4.6: calling sg harness run --gated or API before Modules B and C pass throws PrerequisiteError"""
        b_status = self.get_stage_status("B_dataset")
        c_status = self.get_stage_status("C_training")
        missing: list[str] = []
        if b_status != StageStatus.PASSED.value:
            missing.append("B_dataset")
        if c_status != StageStatus.PASSED.value:
            missing.append("C_training")

        if missing:
            raise PrerequisiteError(
                "Gated harness runs unlock in Module D after Modules B and C pass.",
                missing_stages=missing
            )

    def set_stage_status(
        self,
        stage_id: str,
        status: StageStatus,
        details: Optional[dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> None:
        if stage_id not in self.state["stages"]:
            self.state["stages"][stage_id] = {}

        self.state["stages"][stage_id]["status"] = status.value
        self.state["stages"][stage_id]["error"] = error
        if details:
            self.state["stages"][stage_id].update(details)

        # If a stage passed, unlock the next stage if it was locked or stale
        if status == StageStatus.PASSED:
            idx = STAGE_ORDER.index(stage_id)
            if idx + 1 < len(STAGE_ORDER):
                next_stage = STAGE_ORDER[idx + 1]
                current_next_status = self.get_stage_status(next_stage)
                if current_next_status in (StageStatus.LOCKED.value, StageStatus.STALE.value):
                    self.state["stages"][next_stage]["status"] = StageStatus.READY.value

        # If a stage is re-run or set to running, mark downstream stages stale
        if status in (StageStatus.RUNNING, StageStatus.READY):
            idx = STAGE_ORDER.index(stage_id)
            for downstream in STAGE_ORDER[idx + 1:]:
                if self.get_stage_status(downstream) in (StageStatus.PASSED.value, StageStatus.READY.value):
                    self.state["stages"][downstream]["status"] = StageStatus.STALE.value

        self._save()

    def to_dict(self) -> dict[str, Any]:
        return self.state
