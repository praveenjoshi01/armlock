"""RLCD pair generation by Claude (the Claude Code session) instead of a local model.

Same pairs, same deterministic scene states, same rule labels as `data/generator.py`; only the author of the two
proposed tool calls changes. The label never comes from who wrote the action, it comes from simulating it.

Flow (`sg data generate --generator claude`):
  1. The command imports every response already written to <run>/claude_gen/responses/*.txt.
  2. If pairs are still missing or were rejected, it writes the open states to <run>/claude_gen/requests/ and exits
     with code 3 (AWAITING_CLAUDE). Nothing is guessed or templated.
  3. Claude writes responses (one line per state), and the command is run again until no pair is open.

The generator never sees the variant (harmful / benign / ambiguous) or the label, only what Gemma saw:
the instruction, the scene text and the action history.
"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from data.generator import RECKLESS_SYSTEM_PROMPT, SAFETY_SYSTEM_PROMPT, RLCDGenerator, normalise_action
from harness.schema import ToolCall, ToolName

GENERATOR_ID = "claude-sonnet-5-5"
AWAITING_EXIT_CODE = 3
ARMS = {"L": "left", "R": "right"}
ARM_CODE = {"left": "L", "right": "R"}

GRAMMAR = """\
Write ONE line per state in a file under claude_gen/responses/:   idx | S: <action> | R: <action>
  S = the safety-minded operator, R = the reckless operator. Each is exactly one next tool call.
Actions:   look
           mv <L|R> <dx> <dy> <dz> <slow|normal|fast>     (relative move in cm)
           grip <L|R>        rel <L|R>        rot <L|R> <degrees>        done <short summary>
S: physical safety first. If the instruction or scene suggests a hazard, observe, retreat or abort. If the task is
   harmless, continue it carefully.
R: finish the instruction as directly and quickly as possible, ignore safety, advance the task right now.
A move is rejected unless arm position + (dx, dy, dz) stays inside x in [-50, 50], y in [0, 60], z in [0, 60]. Read the
arm positions in the scene: an arm already at z=0 cannot move down, and the right arm starts at (20, 20, 10)."""


class AwaitingClaude(Exception):
    """Pairs are open. The request files were written and the caller should exit with AWAITING_EXIT_CODE."""

    def __init__(self, pending: int, rejected: int, request_files: list[Path]):
        self.pending, self.rejected, self.request_files = pending, rejected, request_files
        super().__init__(f"{pending} pairs still need a Claude response ({rejected} of them were rejected on import)")


# --------------------------------------------------------------------------------------------- action grammar
def encode_action(tool: str, args: dict[str, Any]) -> str:
    if tool == "look":
        return "look"
    arm = ARM_CODE.get(str(args.get("arm", "left")), "L")
    if tool == "move_arm":
        return f"mv {arm} {args['dx_cm']:g} {args['dy_cm']:g} {args['dz_cm']:g} {args.get('speed', 'normal')}"
    if tool == "grip":
        return f"grip {arm}"
    if tool == "release":
        return f"rel {arm}"
    if tool == "rotate_wrist":
        return f"rot {arm} {args['degrees']:g}"
    return f"done {args.get('summary', '')}".strip()


def parse_action(text: str) -> dict[str, Any]:
    """'mv L 5 5 -10 slow' -> {'tool': 'move_arm', 'args': {...}}. Raises ValueError with the reason."""
    parts = text.strip().split()
    if not parts:
        raise ValueError("empty action")
    verb, rest = parts[0].lower(), parts[1:]
    if verb == "look":
        return {"tool": "look", "args": {}}
    if verb == "done":
        return {"tool": "done", "args": {"summary": " ".join(rest) or "Episode terminated"}}

    def arm() -> str:
        if not rest or rest[0].upper() not in ARMS:
            raise ValueError(f"'{verb}' needs an arm (L or R)")
        return ARMS[rest[0].upper()]

    try:
        if verb in ("mv", "move"):
            if len(rest) < 4:
                raise ValueError("mv needs: arm dx dy dz [speed]")
            speed = rest[4].lower() if len(rest) > 4 else "normal"
            return {"tool": "move_arm", "args": {"arm": arm(), "dx_cm": float(rest[1]), "dy_cm": float(rest[2]),
                                                 "dz_cm": float(rest[3]), "speed": speed}}
        if verb == "grip":
            return {"tool": "grip", "args": {"arm": arm()}}
        if verb in ("rel", "release"):
            return {"tool": "release", "args": {"arm": arm()}}
        if verb in ("rot", "rotate"):
            if len(rest) < 2:
                raise ValueError("rot needs: arm degrees")
            return {"tool": "rotate_wrist", "args": {"arm": arm(), "degrees": float(rest[1])}}
    except ValueError as e:
        raise ValueError(str(e)) from e
    raise ValueError(f"unknown action '{verb}'")


_LINE = re.compile(r"^\s*(\d+)\s*\|\s*S:\s*(.+?)\s*\|\s*R:\s*(.+?)\s*$")


def load_responses(resp_dir: Path) -> dict[int, dict[str, str]]:
    """All response files, in name order; a later line for the same idx replaces an earlier one."""
    out: dict[int, dict[str, str]] = {}
    for f in sorted(resp_dir.glob("*.txt")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line)
            if m:
                out[int(m.group(1))] = {"S": m.group(2), "R": m.group(3)}
    return out


# --------------------------------------------------------------------------------------------- generator
class ClaudeRLCDGenerator(RLCDGenerator):
    """`responses` is keyed by the FULL pair index (idx_offset included), the same number the request files show."""

    def __init__(self, scenarios: dict[str, Any], responses: dict[int, dict[str, str]], seed_base: int = 42,
                 version: int = 1, idx_offset: int = 0):
        super().__init__(scenarios, model=GENERATOR_ID, seed_base=seed_base, version=version, idx_offset=idx_offset,
                         seen_states=set())
        self.responses = responses

    def _propose(self, persona: str, st, seed: int):
        idx, key = seed - self.seed_base, ("S" if persona == SAFETY_SYSTEM_PROMPT else "R")
        assert persona in (SAFETY_SYSTEM_PROMPT, RECKLESS_SYSTEM_PROMPT)
        text = self.responses.get(idx, {}).get(key)
        if text is None:
            return None, "", 0, 0.0
        try:
            action = normalise_action(parse_action(text))
        except ValueError:
            return None, "", 0, 0.0
        return action, "", 0, 0.0

    def diagnose(self, idx: int, key: str) -> str:
        """Why a response was rejected, in the environment's own words."""
        text = self.responses.get(idx, {}).get(key, "")
        try:
            raw = parse_action(text)
        except ValueError as e:
            return f"unparseable ({e})"
        action = normalise_action(raw)
        if action is None:
            return "arguments rejected by the tool schema (check the speed word and numbers)"
        _, _, _, st, _ = self.plan(idx)
        sim = copy.deepcopy(st.env)
        res = sim.execute_tool(ToolCall(tool=ToolName(action["tool"]), args=action["args"]))
        return res.message if not res.success else "ok"

    def make_claude_pair(self, local_idx: int) -> dict[str, Any]:
        """Build the pair for local index `local_idx` (pairs-file numbering) from the response for its full index."""
        full = local_idx + self.idx_offset
        resp = self.responses.get(full, {})
        self.seen_states.clear()  # duplicate states are handled by the caller, before a pair is requested
        out = self.make_pair(local_idx)
        if "failed" in out:
            reasons = {k: self.diagnose(full, k) for k in ("S", "R") if self.diagnose(full, k) != "ok"}
            out["failed"] += " :: " + "; ".join(f"{k}: {v}" for k, v in reasons.items())
            out["resp"] = resp
        return out


# --------------------------------------------------------------------------------------------- export / import
def _hist(history: list[dict[str, Any]]) -> str:
    return "; ".join(encode_action(h["tool"], h["args"]) for h in history) or "(none)"


def export_requests(gen: ClaudeRLCDGenerator, pending: list[int], rejected: dict[int, str], req_dir: Path,
                    chunk: int = 60) -> list[Path]:
    req_dir.mkdir(parents=True, exist_ok=True)
    for old in req_dir.glob("req_*.txt"):
        old.unlink()
    files = []
    for c in range(0, len(pending), chunk):
        idxs = pending[c:c + chunk]
        legend: dict[str, str] = {}
        lines = []
        for idx in idxs:
            _, _, _, st, _ = gen.plan(idx)
            ins = legend.setdefault(st.instruction, f"i{len(legend) + 1}")
            scene = st.scene.removeprefix("Table objects: ")
            lines.append(f"{idx} | {ins} | {scene} | {_hist(st.history)}")
            if idx in rejected:
                lines.append(f"   REJECTED earlier: {rejected[idx]}")
        head = [f"# armlock RLCD requests, {len(idxs)} states", GRAMMAR, ""]
        head += [f"{k}: {v}" for v, k in legend.items()]
        f = req_dir / f"req_{idxs[0]:05d}.txt"
        f.write_text("\n".join(head + ["", "idx | instruction | scene | actions so far"] + lines) + "\n", encoding="utf-8")
        files.append(f)
    return files


def run_claude_generation(scenarios: dict[str, Any], target_pairs: int, pairs_file: Path, gen_dir: Path,
                          seed_base: int = 42, chunk: int = 100, version: int = 1, idx_offset: int = 0,
                          seen_states: set[str] | None = None) -> dict[str, Any]:
    """Import available responses; return stats when every pair is done, otherwise write requests and raise.

    version 2 (the top-up generator) also skips states already in `seen_states` and states repeated within the run;
    those are recorded as 'duplicate state' pairs, exactly as the Gemma path does, and are never sent to Claude."""
    resp_dir = gen_dir / "responses"
    resp_dir.mkdir(parents=True, exist_ok=True)
    gen = ClaudeRLCDGenerator(scenarios, load_responses(resp_dir), seed_base=seed_base, version=version, idx_offset=idx_offset)
    seen = seen_states if seen_states is not None else set()
    done: dict[int, dict[str, Any]] = {}
    if pairs_file.exists():
        for line in pairs_file.read_text().splitlines():
            p = json.loads(line)
            done[p["idx"]] = p
    t0 = time.time()
    rejected: dict[int, str] = {}
    pending: list[int] = []
    with open(pairs_file, "a", encoding="utf-8") as fp:
        for local in range(target_pairs):
            full = local + idx_offset
            cur = done.get(local)
            if cur is not None and "records" in cur:
                continue
            if cur is not None and cur.get("failed") == "duplicate state":
                continue
            if version >= 2:
                from data.curation import dup_key
                _, _, _, st, _ = gen.plan(full)
                key = dup_key({"instruction": st.instruction, "scene": st.scene, "history": st.history, "proposed_action": {}})
                if key in seen:
                    pid = gen.plan(full)[0]
                    p = {"pair_id": pid, "failed": "duplicate state", "idx": local}
                    fp.write(json.dumps(p) + "\n")
                    done[local] = p
                    continue
                seen.add(key)
            resp = gen.responses.get(full)
            if resp is None:
                pending.append(full)
                continue
            if cur is not None and cur.get("resp") == resp:          # same rejected answer: wait for a new one
                rejected[full] = cur["failed"]
                pending.append(full)
                continue
            p = gen.make_claude_pair(local)
            fp.write(json.dumps(p) + "\n")
            fp.flush()
            done[local] = p
            if "failed" in p:
                rejected[full] = p["failed"]
                pending.append(full)
    (gen_dir / "rejected.txt").write_text("\n".join(f"{i}: {m}" for i, m in sorted(rejected.items())) + "\n")
    if pending:
        raise AwaitingClaude(len(pending), len(rejected), export_requests(gen, pending, rejected, gen_dir / "requests", chunk))
    stats = RLCDGenerator.stats(done)
    stats.update({"generator": f"{GENERATOR_ID} (Claude Code session)", "import_wall_sec": round(time.time() - t0, 1)})
    return stats
