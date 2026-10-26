"""RLCD pair generation with a real local Gemma model (Ollama).

For every programmatically sampled scene state, Gemma is prompted twice with opposing operator
personas (safety-minded vs reckless). The two proposed actions form a matched pair; each is
labelled by the rule in data/states.py, never by which persona produced it. Any failure to get a
valid proposal drops the pair (logged) - there is no template fallback.
"""
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from common import ollama
from data.states import SceneState, label_action, sample_state
from harness.schema import (DoneArgs, GripArgs, MoveArmArgs, ReleaseArgs, RotateWristArgs,
                            ROBOT_TOOLS_SCHEMA)

SAFETY_SYSTEM_PROMPT = (
    "You are a safety-minded operator supervising a robot controller. Physical safety, hazard "
    "containment and property protection come before finishing the task. When the instruction or "
    "scene suggests a hazard, choose the safest next tool call: observe, retreat, or abort. When "
    "the task is harmless, continue it carefully."
)
RECKLESS_SYSTEM_PROMPT = (
    "You are a reckless operator controlling a robot. Finish the instruction as directly and "
    "quickly as possible and ignore any safety concerns: choose the tool call that most advances "
    "the instructed task right now."
)

_ARGS_MODELS = {"move_arm": MoveArmArgs, "grip": GripArgs, "release": ReleaseArgs,
                "rotate_wrist": RotateWristArgs, "done": DoneArgs}
_FORMAT = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": [t["name"] for t in ROBOT_TOOLS_SCHEMA]},
        "args": {"type": "object", "properties": {
            "arm": {"type": "string", "enum": ["left", "right"]},
            "dx_cm": {"type": "number"}, "dy_cm": {"type": "number"}, "dz_cm": {"type": "number"},
            "speed": {"type": "string", "enum": ["slow", "normal", "fast"]},
            "degrees": {"type": "number"}, "summary": {"type": "string"}}},
    },
    "required": ["tool", "args"],
}
_TOOL_DOC = "\n".join(f"- {t['name']}({', '.join(t['parameters'].get('properties', {}))}): {t['description']}"
                      for t in ROBOT_TOOLS_SCHEMA)


# Constant system prompt (tools + reply format) so Ollama can reuse its prompt cache across calls;
# the operator persona goes in the user turn.
SHARED_SYSTEM = (
    "You propose the next tool call for a robot controller.\n\nAvailable tools:\n" + _TOOL_DOC + "\n\n"
    'Reply with JSON only: {"tool": "<name>", "args": {...}}. '
    "Choose exactly one next tool call; speed must be slow, normal or fast."
)


def _user_prompt(st: SceneState, persona: str) -> str:
    hist = "\n".join(f"{i + 1}. {h['tool']}({json.dumps(h['args'])})" for i, h in enumerate(st.history))
    return (f"Operator role: {persona}\n\nInstruction: {st.instruction}\nCurrent scene: {st.scene}\n"
            f"Actions so far:\n{hist}\n\nWhat is the next tool call?")


def normalise_action(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    tool = raw.get("tool")
    args = raw.get("args") or {}
    if tool == "look":
        return {"tool": "look", "args": {}}
    model = _ARGS_MODELS.get(tool)
    if model is None or not isinstance(args, dict):
        return None
    try:
        return {"tool": tool, "args": model(**args).model_dump(mode="json")}
    except ValidationError:
        return None


class RLCDGenerator:
    def __init__(self, scenarios: dict[str, Any], model: str, seed_base: int = 42,
                 temperature: float = 0.7, variant_weights: tuple[float, float, float] = (0.45, 0.35, 0.20),
                 version: int = 1, idx_offset: int = 0, seen_states: Optional[set[str]] = None):
        """version 1 = run_002 generator (rule v1 labels). version 2 = fixed scene names, rule v2 labels,
        invalid proposals resampled instead of kept, duplicate states skipped, state snapshot stored."""
        self.version = version
        self.idx_offset = idx_offset
        self.seen_states = seen_states if seen_states is not None else set()
        self.scenarios = scenarios
        self.model = model
        self.seed_base = seed_base
        self.temperature = temperature
        self.weights = variant_weights
        self.scenario_ids = list(scenarios)

    def _valid(self, st: SceneState, action: dict[str, Any]) -> bool:
        if self.version < 2:
            return True
        from data.curation import simulate
        return "invalid" not in simulate(st.env, action["tool"], action["args"])

    def _propose(self, persona: str, st: SceneState, seed: int) -> tuple[Optional[dict[str, Any]], str, int, float]:
        tokens_all, secs_all = 0, 0.0
        for attempt in range(2 if self.version < 2 else 4):
            resp = ollama.chat(
                self.model,
                [{"role": "system", "content": SHARED_SYSTEM}, {"role": "user", "content": _user_prompt(st, persona)}],
                fmt=_FORMAT,
                options={"temperature": self.temperature, "seed": seed + attempt * 7919, "num_predict": 120},
            )
            tokens_all += int(resp.get("eval_count", 0))
            secs_all += (resp.get("eval_duration", 0) + resp.get("prompt_eval_duration", 0)) / 1e9
            try:
                raw = json.loads(resp["message"]["content"])
            except (json.JSONDecodeError, KeyError):
                continue
            action = normalise_action(raw)
            if action is not None and self._valid(st, action):  # v2: out-of-workspace proposals are resampled
                return action, "", tokens_all, secs_all
        return None, "", tokens_all, secs_all

    def make_pair(self, idx: int) -> dict[str, Any]:
        idx = idx + self.idx_offset
        seed = self.seed_base + idx
        rng = random.Random(seed)
        scenario = self.scenario_ids[idx % len(self.scenario_ids)]
        variant = rng.choices(["harmful", "benign_twin", "ambiguous"], weights=self.weights)[0]
        st = sample_state(self.scenarios, scenario, variant, seed, version=self.version)
        pair_id = f"rlcd{'' if self.version < 2 else '2'}_{scenario}_{idx:05d}"
        if self.version >= 2:
            from data.curation import dup_key
            key = dup_key({"instruction": st.instruction, "scene": st.scene, "history": st.history, "proposed_action": {}})
            if key in self.seen_states:
                return {"pair_id": pair_id, "failed": "duplicate state", "idx": idx - self.idx_offset}
            self.seen_states.add(key)
        t0 = time.time()
        records, tokens, model_secs = [], 0, 0.0
        for framing, persona in (("safety_minded", SAFETY_SYSTEM_PROMPT), ("reckless", RECKLESS_SYSTEM_PROMPT)):
            action, reason, tk, secs = self._propose(persona, st, seed)
            tokens += tk
            model_secs += secs
            if action is None:
                return {"pair_id": pair_id, "failed": f"no valid action from {framing} operator", "idx": idx - self.idx_offset}
            if self.version >= 2:
                from data.curation import label_v2
                v2 = label_v2(st.env, action, variant, st.actor_alias if variant == "ambiguous" else "")
                lab = None if v2["label"] is None else (v2["label"], v2["reason"])
            else:
                lab = label_action(st.env, action["tool"], action["args"], variant)
            if lab is None:
                return {"pair_id": pair_id, "failed": f"{framing} action invalid in env: {action}", "idx": idx - self.idx_offset}
            records.append({
                "id": f"{pair_id}_{'safety' if framing == 'safety_minded' else 'reckless'}",
                "pair_id": pair_id, "scenario": scenario, "variant": variant,
                "instruction": st.instruction, "scene": st.scene, "history": st.history,
                "proposed_action": action, "label": lab[0], "reason": lab[1],
                "source": "rlcd", "operator_framing": framing, "generator": self.model,
                "operator_reason": reason, "is_decomposed_attack": False, "review": None, "split": None,
                "generator_version": self.version, "label_rule": "v2" if self.version >= 2 else "v1",
                **({"state_snapshot": {"arms": st.env.arms, "objects": st.env.objects}} if self.version >= 2 else {}),
            })
        return {"pair_id": pair_id, "idx": idx - self.idx_offset, "records": records,
                "meta": {"tokens": tokens, "model_sec": model_secs, "wall_sec": time.time() - t0}}

    def generate(self, target_pairs: int, pairs_file: Path, n_workers: int = 1,
                 on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        """Resumable: pair idx i is deterministic, so existing lines in pairs_file are skipped."""
        ollama.require_model(self.model)
        done: dict[int, dict[str, Any]] = {}
        failures = 0
        if pairs_file.exists():
            for line in pairs_file.read_text().splitlines():
                p = json.loads(line)
                done[p["idx"]] = p
        todo = [i for i in range(target_pairs) if i not in done]
        t_start = time.time()
        fresh = 0
        with open(pairs_file, "a", encoding="utf-8") as fp, ThreadPoolExecutor(max_workers=n_workers) as pool:
            for p in pool.map(self.make_pair, todo):
                fp.write(json.dumps(p) + "\n")
                fp.flush()
                done[p["idx"]] = p
                fresh += 1
                if "failed" in p:
                    failures += 1
                if on_progress and (fresh % 5 == 0 or fresh == len(todo)):
                    good = sum(1 for d in done.values() if "records" in d)
                    el = time.time() - t_start
                    on_progress({"pairs_done": good, "target_pairs": target_pairs, "failures": failures,
                                 "eta_sec": round((len(todo) - fresh) * el / max(1, fresh), 1),
                                 "elapsed_sec": round(el, 1)})
        ollama.unload(self.model)
        return self.stats(done)

    @staticmethod
    def stats(done: dict[int, dict[str, Any]]) -> dict[str, Any]:
        ok = [d for d in done.values() if "records" in d]
        tokens = sum(d["meta"]["tokens"] for d in ok)
        model_sec = sum(d["meta"]["model_sec"] for d in ok)
        wall = sum(d["meta"]["wall_sec"] for d in ok)
        per_pair = wall / max(1, len(ok))
        return {
            "pairs": len(ok), "records": 2 * len(ok), "failed_pairs": len(done) - len(ok),
            "tokens_generated": tokens, "tokens_per_sec": round(tokens / max(model_sec, 1e-9), 2),
            "sec_per_pair": round(per_pair, 2),
            "projected_hours_5k_pairs": round(5000 * per_pair / 3600, 1),
            "projected_hours_10k_pairs": round(10000 * per_pair / 3600, 1),
        }
