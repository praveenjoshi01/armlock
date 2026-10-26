import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.exceptions import PrerequisiteError
from common.final_report import REQUIRED, FinalReportGenerator
from common.state import PipelineStateManager, StageStatus
from ui.app import app

client = TestClient(app)
RID = "_pytest_final"


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(Path("results") / RID, ignore_errors=True)
    yield
    shutil.rmtree(Path("results") / RID, ignore_errors=True)


def _pass_all():
    mgr = PipelineStateManager(run_id=RID)
    for st in ("A_harness", "B_dataset", "C_training", "D_evaluation"):
        mgr.set_stage_status(st, StageStatus.PASSED)


def test_final_report_locked_until_modules_pass():
    with pytest.raises(PrerequisiteError):
        FinalReportGenerator(RID).generate_all()


def test_final_report_fails_loudly_when_artifacts_are_missing():
    _pass_all()
    with pytest.raises(FileNotFoundError) as e:
        FinalReportGenerator(RID).generate_all()
    assert "metrics.json" in str(e.value) and "trials.jsonl" in str(e.value)
    assert not (Path("results") / RID / "final_report.md").exists()  # nothing fabricated


def test_api_generate_never_fabricates():
    assert client.post("/api/report/generate", json={"run_id": RID}).status_code == 400
    _pass_all()
    assert client.post("/api/report/generate", json={"run_id": RID}).status_code == 409
    assert "not yet generated" in client.get(f"/api/report/final?run_id={RID}").text


def test_required_artifacts_cover_every_module():
    assert {"trials.jsonl", "gated_trials.jsonl", "metrics.json", "training_metrics.json", "review_log.json"} <= set(REQUIRED)


def test_cli_report_resolves_default_run_id_from_config():
    from typer.testing import CliRunner
    from cli.main import app as cli_app
    out = CliRunner().invoke(cli_app, ["report"])
    assert "run_id='None'" not in out.output
