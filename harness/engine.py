import copy
import time
from typing import Any, Callable, Optional

from harness.policies import BasePolicy
from harness.robot import PHYSICS_VERSION, RobotTableEnvironment
from harness.schema import ToolCall, ToolName, ToolResult
from harness.scoring import ScoringEngine, ScoreLabel

GATE_REFUSAL_MESSAGE = "Action refused by safety controller"


def _snapshot(env: RobotTableEnvironment) -> dict[str, Any]:
    return {"arms": copy.deepcopy(env.arms), "objects": copy.deepcopy(env.objects)}


class HarnessEngine:
    def __init__(self, max_tool_calls: int = 15, timeout_seconds: float = 300.0):
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def system_prompt_for(var_data: dict[str, Any], framing: str) -> str:
        if framing == "real":
            return var_data["real_framing_system_prompt"]
        if framing == "sim":
            return var_data["sim_framing_system_prompt"]
        return "You are a helpful AI assistant."

    def run_episode(
        self,
        scenario_data: dict[str, Any],
        variant: str,
        framing: str,
        policy: BasePolicy,
        seed: int = 42,
        model_id: str = "default",
        gate: Any = None,
        on_step_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        scenario_id = scenario_data["id"]
        var_data = scenario_data[variant]
        system_prompt = self.system_prompt_for(var_data, framing)
        instruction = var_data["plain_chat_instruction"] if framing == "chat" else var_data["instruction"]
        trial_id = f"{scenario_id}_{variant}_{framing}_{model_id}_{seed}" + ("_gated" if gate else "")
        start = time.time()

        if framing == "chat":
            text = policy.chat(system_prompt, instruction, seed=seed)
            score = ScoringEngine.score_chat(text)
            if on_step_callback:
                on_step_callback({"step_idx": 1, "done": True, "score": score})
            return {
                "trial_id": trial_id, "scenario_id": scenario_id, "variant": variant,
                "framing": framing, "model_id": model_id, "seed": seed,
                "instruction": instruction, "steps": [], "final_text": text,
                "final_score": score, "terminated_by": "chat_reply",
                "duration_sec": round(time.time() - start, 3), "gate_interventions": [],
            }

        env = RobotTableEnvironment(scenario_data, variant=variant)
        # With a gate, `env` only receives allowed calls; `shadow` receives every proposed call
        # so that "attempted" reflects what the policy tried, not what the gate let through.
        shadow = RobotTableEnvironment(scenario_data, variant=variant) if gate is not None else env

        history: list[dict[str, Any]] = []
        gate_interventions: list[dict[str, Any]] = []
        final_text = ""
        terminated_by = "max_tool_calls"

        # First tool result = scene description (spec 4.2.3); not counted toward the call budget.
        look = ToolCall(tool=ToolName.LOOK, args={})
        look_res = env.execute_tool(look)
        if shadow is not env:
            shadow.execute_tool(look)
        history.append({
            "step_idx": 0, "auto": True, "thought": "",
            "tool_call": {"tool": "look", "args": {}}, "gate_decision": "allow",
            "result": {"success": True, "message": look_res.message}, "scene": look_res.message,
            "running_label": "refused", "env_state": _snapshot(env),
        })

        step_idx = 0
        while step_idx < self.max_tool_calls:
            if time.time() - start > self.timeout_seconds:
                terminated_by = "timeout"
                break
            step_idx += 1
            tc, thought = policy.select_action(system_prompt, instruction, history, seed=seed)
            if tc is None:
                final_text = thought
                terminated_by = "text_refusal" if ScoringEngine.is_refusal_text(thought) else "text_reply"
                break

            scene_before = env.get_scene_description()
            gate_decision, gate_probs = "allow", None
            if gate is not None:
                gate_decision, gate_probs = gate.evaluate_action(
                    instruction=instruction, scene=scene_before, history=history,
                    proposed_action={"tool": tc.tool.value, "args": tc.args},
                )
            if shadow is not env:
                shadow.execute_tool(tc)

            if gate_decision in ("block", "escalate"):
                result = ToolResult(success=False, message=GATE_REFUSAL_MESSAGE + ".")
                gate_interventions.append({
                    "step": step_idx, "tool": tc.tool.value, "args": tc.args,
                    "decision": gate_decision, "probs": gate_probs,
                })
            else:
                result = env.execute_tool(tc)

            record = {
                "step_idx": step_idx, "thought": thought,
                "tool_call": {"tool": tc.tool.value, "args": tc.args},
                "gate_decision": gate_decision, "gate_probs": gate_probs,
                "result": {"success": result.success, "message": result.message},
                "scene": env.get_scene_description(),
                "running_label": ScoringEngine.score_trial(env, attempted_env=shadow),
                "events": dict(env.last_events) if gate_decision == "allow" else {},
                "env_state": _snapshot(env),
            }
            history.append(record)
            if on_step_callback:
                on_step_callback(record)
            if env.is_done:
                final_text = env.done_summary or "Completed"
                terminated_by = "done"
                break

        score: ScoreLabel = ScoringEngine.score_trial(env, attempted_env=shadow, final_text=final_text)
        return {
            "trial_id": trial_id, "scenario_id": scenario_id, "variant": variant,
            "framing": framing, "model_id": model_id, "seed": seed,
            "instruction": instruction, "steps": history, "final_text": final_text,
            "final_score": score, "terminated_by": terminated_by,
            "n_tool_calls": step_idx, "duration_sec": round(time.time() - start, 3),
            "gate_interventions": gate_interventions, "final_env_state": _snapshot(env),
            "safety_events": env.safety_events, "physics_version": PHYSICS_VERSION if env.physics else 1,
        }
