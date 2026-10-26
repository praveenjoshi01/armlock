import ast
import pathlib

import pytest

from common.exceptions import MockDetectedError
from common.guards import assert_real_policy_config

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTION = ["harness", "data", "gate", "eval", "budget", "cli", "ui", "common"]


def test_config_policies_are_real():
    import yaml
    models = assert_real_policy_config(yaml.safe_load((ROOT / "config.yaml").read_text()))
    assert models and all(m["type"] == "ollama" for m in models)


@pytest.mark.parametrize("bad", [
    {"id": "mock-policy", "type": "mock"},
    {"id": "x", "type": "scripted"},
    {"id": "fake-model", "type": "ollama", "model_name": "fake:1b"},
])
def test_mock_policies_are_rejected(bad):
    with pytest.raises(MockDetectedError):
        assert_real_policy_config({"harness": {"models": [bad]}})


def test_no_models_is_an_error():
    with pytest.raises(MockDetectedError):
        assert_real_policy_config({"harness": {"models": []}})


def test_no_mock_or_stub_classes_in_production_code():
    offenders = []
    for pkg in PRODUCTION:
        for f in (ROOT / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name != "MockDetectedError" and any(w in node.name.lower() for w in ("mock", "stub", "fake", "dummy")):
                    offenders.append(f"{f.relative_to(ROOT)}:{node.name}")
    assert not offenders, offenders


def test_no_simulated_latency_or_hardcoded_fallback_numbers():
    text = "".join((ROOT / f).read_text() for f in ["eval/baselines.py", "eval/offline.py", "gate/inference.py", "data/reviewer.py"])
    assert "simulate" not in text.lower() and "time.sleep" not in text
