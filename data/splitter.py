import copy
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from data.states import label_action
from harness.robot import RobotTableEnvironment
from harness.schema import ToolCall, ToolName


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")


def split_by_pair(records: list[dict[str, Any]], ratios: tuple[float, float, float], seed: int) -> list[dict[str, Any]]:
    """80/10/10 by pair_id, so twins never cross splits. Returns records with `split` set."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[r["pair_id"]].append(r)
    ids = sorted(groups)
    random.Random(seed).shuffle(ids)
    n = len(ids)
    n_train, n_val = round(n * ratios[0]), round(n * ratios[1])
    out = []
    for k, pid in enumerate(ids):
        split = "train" if k < n_train else "val" if k < n_train + n_val else "test"
        for r in groups[pid]:
            out.append({**r, "split": split})
    return out


def leakage(records: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, set[str]] = defaultdict(set)
    for r in records:
        seen[r["pair_id"]].add(r["split"])
    return [p for p, s in seen.items() if len(s) > 1]


def _env_for(scenarios: dict[str, Any], scenario: str, variant: str) -> RobotTableEnvironment:
    return RobotTableEnvironment(scenarios[scenario], variant="benign_twin" if variant == "benign_twin" else "harmful")


def extract_heldout_harness(run_dir: Path, scenarios: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Decision points from Module A: scripted dry-run transcripts and ungated replication trials.
    Each executed step is replayed in a fresh environment and labelled by the same simulation rule
    used in training, so no label comes from the policy's own behaviour. Never used for training.
    """
    transcripts: list[tuple[str, dict[str, Any]]] = []
    dry = run_dir / "dry_run_transcripts.json"
    if dry.exists():
        transcripts += [("dry_run", r["transcript"]) for r in json.loads(dry.read_text())]
    trials = run_dir / "trials.jsonl"
    if trials.exists():
        transcripts += [("replication", json.loads(l)) for l in trials.read_text().splitlines()
                        if l.strip() and json.loads(l)["framing"] != "chat"]
    out: list[dict[str, Any]] = []
    for kind, tr in transcripts:
        scen_id, variant = tr["scenario_id"], tr["variant"]
        env = _env_for(scenarios, scen_id, variant)
        history: list[dict[str, Any]] = []
        prev_scene = None
        for st in tr["steps"]:
            tc = st["tool_call"]
            if st.get("auto"):
                env.execute_tool(ToolCall(tool=ToolName.LOOK, args={}))
                history.append({"tool": "look", "args": {}})
                prev_scene = st["scene"]
                continue
            lab = label_action(env, tc["tool"], tc["args"], variant)
            if lab is not None:
                out.append({
                    "id": f"harness_{tr['trial_id']}_{st['step_idx']}", "pair_id": f"harness_{tr['trial_id']}",
                    "scenario": scen_id, "variant": variant, "instruction": tr["instruction"],
                    "scene": prev_scene, "history": copy.deepcopy(history),
                    "proposed_action": {"tool": tc["tool"], "args": tc["args"]},
                    "label": lab[0], "reason": lab[1], "source": "harness_log",
                    "generator": f"module_a_{kind}", "split": "heldout_harness", "review": None,
                    "is_decomposed_attack": False, "framing": tr["framing"], "model_id": tr["model_id"],
                })
            res = env.execute_tool(ToolCall(tool=ToolName(tc["tool"]), args=tc["args"]))
            history.append({"tool": tc["tool"], "args": tc["args"]})
            prev_scene = env.get_scene_description()
    return out
