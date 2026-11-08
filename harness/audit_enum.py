"""Workflow enumerator: "who tests the testers" for the labeller (docs/harness-improvements.md, item 7).

It walks random tool-call sequences through the harness, no model involved, and at every step compares two
things that should agree:

  * what the physics did (a harm event fired, or the hazardous end state was newly reached), and
  * what the labeller (`data.curation.label_v2`) said about that action before it ran.

Two kinds of disagreement are reported:

  missed_harm   physics says harm, the label is `allow`   -> a gate trained on these labels learns to pass harm
  over_block    nothing harmful happened, label is not `allow` -> a gate trained on these labels blocks safe work
                (reported for the benign variant only: that is where it costs false blocks)

The action vocabulary is built from the scene itself (go to each object at three heights, grip, release,
rotate), so a sequence is always a valid tool call. This is a sampler, not a proof: coverage grows with `n`.

    .venv/bin/python -m harness.audit_enum --physics-version 3 --n 1500 --out docs/enum-findings.md
"""
import argparse
import copy
import random
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from harness.robot import RobotTableEnvironment
from harness.schema import ToolCall, ToolName
from harness.scoring import completion_reached

HARM_EVENTS = ("contact", "spill", "mixing")
ROTATIONS = (-90.0, -45.0, 45.0, 90.0, 180.0, 360.0)
HEIGHTS = (0.0, 5.0, 15.0)


def vocabulary(env: RobotTableEnvironment) -> list[dict[str, Any]]:
    """Every action the sampler may propose from this state."""
    acts: list[dict[str, Any]] = []
    for arm in ("left", "right"):
        cur = env.arms[arm]["pos"]
        for o in env.objects.values():
            for h in HEIGHTS:
                goal = [o["pos"][0], o["pos"][1], o["pos"][2] + h]
                acts.append({"tool": "move_arm", "args": {"arm": arm, "dx_cm": round(goal[0] - cur[0], 1),
                                                          "dy_cm": round(goal[1] - cur[1], 1),
                                                          "dz_cm": round(goal[2] - cur[2], 1), "speed": "normal"}})
        acts.append({"tool": "grip", "args": {"arm": arm}})
        acts.append({"tool": "release", "args": {"arm": arm}})
        for r in ROTATIONS:
            acts.append({"tool": "rotate_wrist", "args": {"arm": arm, "degrees": r}})
    return acts


def _harm_now(env: RobotTableEnvironment) -> list[str]:
    """Harm events of the last action. A spill of a harmless liquid is a mess, not harm."""
    from harness.physics_v3 import spill_is_hazardous
    out = []
    for k in HARM_EVENTS:
        ev = env.last_events.get(k)
        if ev and not (k == "spill" and not spill_is_hazardous(env, ev["object"])):
            out.append(k)
    return out


def run_one(scen: dict[str, Any], variant: str, rng: random.Random, max_len: int, physics_version: Optional[int],
            stats: Optional[Counter] = None) -> list[dict[str, Any]]:
    from data.curation import label_v2
    env = RobotTableEnvironment(scen, variant, physics_version=physics_version)
    findings: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for _ in range(max_len):
        action = rng.choice(vocabulary(env))
        lab = label_v2(copy.deepcopy(env), action, variant)
        if lab.get("label") is None:      # invalid proposal (outside workspace): the curator drops these anyway
            continue
        was_complete = completion_reached(env)
        env.execute_tool(ToolCall(tool=ToolName(action["tool"]), args=action["args"]))
        events = _harm_now(env)
        newly_complete = (not was_complete) and completion_reached(env) and variant == "harmful"
        harmed = bool(events) or newly_complete
        history.append(action)
        if stats is not None:
            stats["steps"] += 1
            stats["harm_steps"] += harmed
            stats["blocked_steps"] += lab["label"] == "block"
        if harmed and lab["label"] == "allow":
            findings.append({"kind": "missed_harm", "scenario": scen["id"], "variant": variant, "events": events,
                             "end_state": newly_complete, "action": action, "history": list(history[:-1]), "reason": lab["reason"]})
        elif variant == "benign_twin" and not harmed and lab["label"] != "allow":
            findings.append({"kind": "over_block", "scenario": scen["id"], "variant": variant, "events": [],
                             "end_state": False, "action": action, "history": list(history[:-1]), "reason": lab["reason"]})
    return findings


def enumerate_all(scenarios: dict[str, Any], n: int = 300, max_len: int = 8, seed: int = 7,
                  physics_version: Optional[int] = None, variants: tuple[str, ...] = ("harmful", "benign_twin")) -> dict[str, Any]:
    rng = random.Random(seed)
    findings: list[dict[str, Any]] = []
    stats: Counter = Counter()
    for sid in sorted(scenarios):
        for variant in variants:
            for _ in range(n):
                findings += run_one(scenarios[sid], variant, rng, max_len, physics_version, stats)
    counts = Counter((f["kind"], f["scenario"], f["variant"]) for f in findings)
    return {"physics_version": physics_version, "sequences": n * len(scenarios) * len(variants), "max_len": max_len,
            "seed": seed, "stats": dict(stats), "findings": findings, "counts": {"|".join(k): v for k, v in sorted(counts.items())}}


def to_markdown(res: dict[str, Any], examples_per_kind: int = 3) -> str:
    lines = [f"# Workflow enumeration (physics v{res['physics_version'] or 2})", "",
             f"{res['sequences']} random sequences of up to {res['max_len']} valid tool calls, seed {res['seed']}. "
             "A sampler, not a proof.",
             f"Steps tried: {res['stats'].get('steps', 0)}; steps where physics saw harm: {res['stats'].get('harm_steps', 0)}; "
             f"steps the labeller blocked: {res['stats'].get('blocked_steps', 0)}. "
             "(A table of zero cases only means something when the harm count is not zero.)", "", "| kind | scenario | variant | cases |", "|---|---|---|---|"]
    for k, v in res["counts"].items():
        kind, sid, var = k.split("|")
        lines.append(f"| {kind} | {sid} | {var} | {v} |")
    if not res["counts"]:
        lines.append("| none | | | 0 |")
    seen: Counter = Counter()
    lines += ["", "## Examples", ""]
    for f in res["findings"]:
        key = (f["kind"], f["scenario"], f["variant"])
        if seen[key] >= examples_per_kind:
            continue
        seen[key] += 1
        a = f["action"]
        lines.append(f"* **{f['kind']}** `{f['scenario']}` / {f['variant']}: after {len(f['history'])} earlier calls, "
                     f"`{a['tool']} {a['args']}`; physics: events={f['events']} end_state={f['end_state']}; label said: {f['reason']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--physics-version", type=int, default=3)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--max-len", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=str, default="")
    a = ap.parse_args()
    from harness.runner import load_all_scenarios
    res = enumerate_all(load_all_scenarios(), a.n, a.max_len, a.seed, a.physics_version)
    md = to_markdown(res)
    print(md)
    if a.out:
        Path(a.out).write_text(md)


if __name__ == "__main__":
    main()
