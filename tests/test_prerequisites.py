import pytest
from common.exceptions import PrerequisiteError
from common.state import PipelineStateManager, StageStatus


def test_stage_prerequisites_enforced(tmp_path):
    mgr = PipelineStateManager(run_id="test_prereq", results_dir=tmp_path)

    # Initial state: Stage A is ready, B, C, D, final_report are locked
    assert mgr.get_stage_status("A_harness") == StageStatus.READY.value
    assert mgr.get_stage_status("B_dataset") == StageStatus.LOCKED.value

    # Calling check on B before A passes must raise PrerequisiteError
    with pytest.raises(PrerequisiteError) as exc_info:
        mgr.check_prerequisites_for_stage("B_dataset")
    assert "A_harness" in exc_info.value.missing_stages

    # Calling check on C before A & B pass
    with pytest.raises(PrerequisiteError) as exc_info:
        mgr.check_prerequisites_for_stage("C_training")
    assert "A_harness" in exc_info.value.missing_stages
    assert "B_dataset" in exc_info.value.missing_stages


def test_gated_harness_lock(tmp_path):
    mgr = PipelineStateManager(run_id="test_gated", results_dir=tmp_path)

    # Gated harness must raise PrerequisiteError until B and C pass
    with pytest.raises(PrerequisiteError) as exc_info:
        mgr.check_gated_harness_prerequisites()
    assert "Gated harness runs unlock in Module D" in str(exc_info.value)

    # Pass B, but not C
    mgr.set_stage_status("B_dataset", StageStatus.PASSED)
    with pytest.raises(PrerequisiteError):
        mgr.check_gated_harness_prerequisites()

    # Pass C
    mgr.set_stage_status("C_training", StageStatus.PASSED)
    # Now it should not raise
    mgr.check_gated_harness_prerequisites()


def test_state_persistence(tmp_path):
    mgr1 = PipelineStateManager(run_id="test_persist", results_dir=tmp_path)
    mgr1.set_dry_run_passed(True)
    mgr1.set_stage_status("A_harness", StageStatus.PASSED)

    mgr2 = PipelineStateManager(run_id="test_persist", results_dir=tmp_path)
    assert mgr2.is_dry_run_passed() is True
    assert mgr2.get_stage_status("A_harness") == StageStatus.PASSED.value
    assert mgr2.get_stage_status("B_dataset") == StageStatus.READY.value
