"""Minimal Ollama client. Every failure raises: nothing is silently replaced by a fallback."""
from typing import Any, Optional

import httpx

from common.exceptions import ModelUnavailableError

BASE_URL = "http://localhost:11434"


def require_model(model: str, base_url: str = BASE_URL) -> dict[str, Any]:
    try:
        tags = httpx.get(f"{base_url}/api/tags", timeout=10).json()
    except Exception as e:
        raise ModelUnavailableError(f"Ollama not reachable at {base_url}: {e}") from e
    for m in tags.get("models", []):
        if model in (m.get("name"), m.get("model")):
            return {"name": m["name"], "digest": m.get("digest", ""), "size": m.get("size", 0)}
    raise ModelUnavailableError(f"Model {model!r} is not installed (run `ollama pull {model}`).")


def chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    fmt: Optional[dict[str, Any]] = None,
    options: Optional[dict[str, Any]] = None,
    keep_alive: str = "10m",
    timeout: float = 600.0,
    base_url: str = BASE_URL,
) -> dict[str, Any]:
    # Pin a small context: left unset, Ollama loads gemma3 with a 131k context whose KV cache grew to ~13 GB
    # and slowed generation ~7x over a few hours (run_003 top-up). Our prompts are < 1k tokens.
    opts = {"num_ctx": 4096, **(options or {})}
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                               "think": False, "keep_alive": keep_alive, "options": opts}
    if fmt is not None:
        payload["format"] = fmt
    try:
        r = httpx.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise ModelUnavailableError(f"Ollama chat failed for {model}: {e}") from e
    if r.status_code != 200:
        raise ModelUnavailableError(f"Ollama HTTP {r.status_code} for {model}: {r.text[:300]}")
    return r.json()


def unload(model: str, base_url: str = BASE_URL) -> None:
    """Free the model's memory (spec: one large model in memory at a time)."""
    try:
        httpx.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except httpx.HTTPError:
        pass
