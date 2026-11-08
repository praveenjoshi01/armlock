"""Who ran this trial? A short fingerprint of the simulator that produced a record.

Output-only scoring cannot tell that the physics, the scenarios or the scorer changed between two runs
(see docs/harness-improvements.md, item 4). Every trial and every harness report carries this fingerprint
so a result can be tied to the exact harness code and scenario files that made it.
"""
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent


def _digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def _hashes() -> tuple[str, str]:
    code = _digest([p for p in (_ROOT / "harness").glob("*.py") if p.name != "__init__.py"])
    scen = _digest(list((_ROOT / "scenarios").glob("*.yaml")))
    return code, scen


def harness_identity(physics_version: int) -> dict[str, Any]:
    code, scen = _hashes()
    return {"physics_version": physics_version, "harness_code": code, "scenarios": scen}
