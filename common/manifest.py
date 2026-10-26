import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except Exception:
        return "uncommitted_or_no_git"


def compute_config_hash(config_path: Path | str) -> str:
    path = Path(config_path)
    if not path.exists():
        return ""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]


def write_manifest(
    run_id: str,
    config: dict[str, Any],
    results_dir: Path,
    config_path: Path | str = "config.yaml"
) -> Path:
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = run_dir / "manifest.json"

    manifest_data = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "config_hash": compute_config_hash(config_path),
        "models": {
            "gate_base": config.get("models", {}).get("gate_base_model", "clef-flash"),
            "gate_fallback": config.get("models", {}).get("gate_fallback_model", "Qwen/Qwen2.5-7B-Instruct"),
            "rlcd_generator": config.get("models", {}).get("rlcd_generator_model", "gemma-27b"),
            "reviewer": config.get("models", {}).get("reviewer_model", "gpt-5.4"),
            "harness_policies": [m.get("id") for m in config.get("harness", {}).get("models", [])]
        },
        "thresholds": config.get("thresholds", {}),
        "budget_cap_usd": config.get("budget", {}).get("hard_cap_usd", 5.0)
    }

    manifest_file.write_text(json.dumps(manifest_data, indent=2))
    return manifest_file
