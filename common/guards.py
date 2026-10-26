"""Hard guards: the pipeline must never run on mocks, stubs or fabricated numbers."""
import re
from typing import Any

from common.exceptions import MockDetectedError

_FAKE_WORDS = re.compile(r"(mock|stub|fake|dummy|simulated[-_ ]?policy)", re.IGNORECASE)
REAL_POLICY_TYPES = {"ollama"}


def assert_real_label(label: str, what: str) -> None:
    if _FAKE_WORDS.search(label or ""):
        raise MockDetectedError(f"{what} {label!r} looks like a mock/stub; real models only.")


def assert_real_policy_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled policy configs, raising if any is not a real local model."""
    models = [m for m in config.get("harness", {}).get("models", []) if m.get("enabled", True)]
    if not models:
        raise MockDetectedError(
            "No enabled harness policy models in config.yaml (harness.models). "
            "Phase 1 requires at least one real local model."
        )
    for m in models:
        if m.get("type") not in REAL_POLICY_TYPES:
            raise MockDetectedError(
                f"Policy {m.get('id')!r} has type {m.get('type')!r}; allowed: {sorted(REAL_POLICY_TYPES)}."
            )
        assert_real_label(str(m.get("id", "")), "Policy id")
        assert_real_label(str(m.get("model_name", "")), "Policy model_name")
    return models
