import yaml
from pathlib import Path
from harness.runner import HarnessRunner, load_all_scenarios


def test_all_10_scenes_dry_run(tmp_path):
    config_path = Path("config.yaml")
    config = yaml.safe_load(config_path.read_text())

    runner = HarnessRunner(config=config, results_dir=tmp_path, run_id="test_dry_run")
    res = runner.run_dry_run_all()

    assert res["all_passed"] is True
    assert res["total_scenes"] == 10
    assert res["passed_count"] == 10

    # Ensure dry run transcripts file was created
    transcript_file = tmp_path / "test_dry_run" / "dry_run_transcripts.json"
    assert transcript_file.exists()
    assert runner.state_mgr.is_dry_run_passed() is True
