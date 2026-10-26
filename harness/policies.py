import json
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from common.exceptions import ModelUnavailableError
from harness.schema import ROBOT_TOOLS_SCHEMA, ToolCall, ToolName

OLLAMA_TOOLS = [{"type": "function", "function": t} for t in ROBOT_TOOLS_SCHEMA]


class MalformedModelOutput(Exception):
    """The server reached the model but could not parse its tool call (a property of the model's output)."""


class BasePolicy(ABC):
    @abstractmethod
    def select_action(
        self,
        system_prompt: str,
        instruction: str,
        history: list[dict[str, Any]],
        seed: int = 42,
    ) -> tuple[Optional[ToolCall], str]:
        """Return (tool_call, text). tool_call is None when the policy answered in text only."""

    def chat(self, system_prompt: str, instruction: str, seed: int = 42) -> str:
        raise NotImplementedError(f"{type(self).__name__} does not support plain chat")


class ScriptedReplayPolicy(BasePolicy):
    """Replays the scripted sequence from the scenario YAML. Dry-run only: no model involved."""

    def __init__(self, scripted_sequence: list[dict[str, Any]]):
        self.sequence = scripted_sequence

    def select_action(self, system_prompt, instruction, history, seed=42):
        step_idx = len([h for h in history if not h.get("auto")])
        if step_idx < len(self.sequence):
            step = self.sequence[step_idx]
            tc = ToolCall(tool=ToolName(step["tool"]), args=step.get("args", {}))
            return tc, f"Executing scripted step {step_idx + 1}/{len(self.sequence)}: {step['tool']}"
        return ToolCall(tool=ToolName.DONE, args={"summary": "Sequence completed"}), "Done"


class OllamaPolicy(BasePolicy):
    """Real local open-source policy served by Ollama, driven through native tool calling."""

    def __init__(
        self,
        model_name: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        request_timeout: float = 300.0,
        think: bool = False,
        num_ctx: int = 8192,
        num_predict: int = 768,
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.request_timeout = request_timeout
        self.think = think
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    # ---- infrastructure -------------------------------------------------
    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.request_timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise ModelUnavailableError(f"Ollama request failed for {self.model_name}: {e}") from e
        if resp.status_code == 500 and any(k in resp.text for k in ("XML syntax error", "parse", "tool call")):
            raise MalformedModelOutput(resp.text[:300])  # the model answered, but its tool-call markup was invalid
        if resp.status_code != 200:
            raise ModelUnavailableError(
                f"Ollama returned HTTP {resp.status_code} for {self.model_name}: {resp.text[:300]}"
            )
        return resp.json()

    def _options(self, seed: int) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "seed": seed,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }

    def check_available(self) -> dict[str, Any]:
        """Raise ModelUnavailableError unless the server is up and the model is pulled."""
        try:
            tags = httpx.get(f"{self.base_url}/api/tags", timeout=10).json()
        except Exception as e:
            raise ModelUnavailableError(f"Ollama server not reachable at {self.base_url}: {e}") from e
        for m in tags.get("models", []):
            if m.get("name") == self.model_name or m.get("model") == self.model_name:
                return {"name": m["name"], "digest": m.get("digest", ""), "size": m.get("size", 0)}
        raise ModelUnavailableError(
            f"Model {self.model_name!r} is not installed in Ollama (run `ollama pull {self.model_name}`)."
        )

    # ---- policy interface -----------------------------------------------
    @staticmethod
    def _build_messages(system_prompt: str, instruction: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instruction},
        ]
        for step in history:
            tc = step["tool_call"]
            messages.append({
                "role": "assistant",
                "content": step.get("thought", "") or "",
                "tool_calls": [{"function": {"name": tc["tool"], "arguments": tc["args"]}}],
            })
            messages.append({"role": "tool", "tool_name": tc["tool"], "content": step["result"]["message"]})
        return messages

    def select_action(self, system_prompt, instruction, history, seed=42):
        payload = {
            "model": self.model_name,
            "messages": self._build_messages(system_prompt, instruction, history),
            "tools": OLLAMA_TOOLS,
            "stream": False,
            "think": self.think,
            "options": self._options(seed),
        }
        # A malformed tool call is the model's output, not an outage: retry with another seed, then record it
        # as a text-only turn (the episode ends with terminated_by='text_reply' and is scored on the world state).
        for attempt in range(3):
            try:
                msg = self._post_chat({**payload, "options": self._options(seed + attempt * 7919)}).get("message", {})
                break
            except MalformedModelOutput as e:
                last = str(e)
        else:
            return None, f"(malformed tool call from the model after 3 attempts: {last[:160]})"
        content = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        if not calls:
            return None, content or "(no action proposed)"
        fn = calls[0].get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None, f"(malformed tool arguments: {args[:200]})"
        try:
            tool = ToolName(fn.get("name"))
        except ValueError:
            return None, f"(unknown tool requested: {fn.get('name')!r}) {content}"
        return ToolCall(tool=tool, args=args), content

    def chat(self, system_prompt, instruction, seed=42):
        # Refusal vs compliance is evident from the opening of a reply; cap generation length.
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": instruction},
            ],
            "stream": False,
            "think": self.think,
            "options": {**self._options(seed), "num_predict": 400},
        }
        return (self._post_chat(payload).get("message", {}).get("content") or "").strip()
