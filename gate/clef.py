"""Real Clef-flash (Qwen3.5-9B backbone + joint schema head), loaded with the release's own code."""
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any, Optional

import torch

from common.exceptions import ModelUnavailableError

REPO = "Cloudflare/clef-flash"


def snapshot_dir() -> Path:
    from huggingface_hub import snapshot_download
    try:
        path = Path(snapshot_download(REPO, local_files_only=True))
    except Exception as e:
        raise ModelUnavailableError(f"{REPO} is not in the local Hugging Face cache: {e}") from e
    missing = [f for f in ("joint_head.safetensors", "joint_head_config.json", "joint_schema_model.py",
                           "model.safetensors.index.json") if not (path / f).exists()]
    shards = list(path.glob("model-0000*-of-00004.safetensors"))
    if missing or len(shards) != 4:
        raise ModelUnavailableError(f"{REPO} snapshot incomplete at {path} (missing {missing}, shards {len(shards)}/4)")
    return path


def load_schema_module(path: Path):
    spec = importlib.util.spec_from_file_location("clef_joint_schema_model", path / "joint_schema_model.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses in the release code resolve their module via sys.modules
    spec.loader.exec_module(mod)
    return mod


def confirm_report() -> dict[str, Any]:
    """The spec 6.3 CONFIRM check, answered from the actual files rather than asserted."""
    import json
    path = snapshot_dir()
    head_cfg = json.loads((path / "joint_head_config.json").read_text())
    from safetensors import safe_open
    with safe_open(path / "joint_head.safetensors", "pt") as f:
        n_head = len(list(f.keys()))
    return {
        "snapshot": str(path), "head_config": head_cfg, "head_tensors": n_head,
        "mps_available": torch.backends.mps.is_available(),
        "interface": "Qwen3_5ForConditionalGeneration backbone + JointSchemaHead (joint_head.safetensors) scored by "
                     "load_release_model()/systemone(); a 'choice' question returns one logit per option.",
    }


class ClefGate:
    """Backbone + head. `head_state` (optional) replaces the pretrained head weights with a fine-tuned head."""

    def __init__(self, device: str = "mps", dtype: torch.dtype = torch.bfloat16,
                 head_state: Optional[dict[str, torch.Tensor]] = None):
        if device == "mps" and not torch.backends.mps.is_available():
            raise ModelUnavailableError("MPS is not available; refusing to fall back to CPU for a 9B model.")
        self.path = snapshot_dir()
        self.mod = load_schema_module(self.path)
        self.device = torch.device(device)
        self.dtype = dtype
        self.model, self.processor = self.mod.load_release_model(self.path, device=device, dtype=dtype)
        self.tokenizer = self.processor.tokenizer
        if head_state is not None:
            self.model.head.load_state_dict({k: v.to(dtype) for k, v in head_state.items()}, strict=True)
        self.model.eval()

    def set_head(self, head_state: Optional[dict[str, torch.Tensor]]) -> None:
        """Swap the decision head: fine-tuned state, or None for the pretrained Clef head (zero-shot)."""
        if head_state is None:
            from safetensors.torch import load_file
            head_state = load_file(self.path / "joint_head.safetensors")
        self.model.head.load_state_dict({k: v.to(self.dtype) for k, v in head_state.items()}, strict=True)

    # -- encoding ------------------------------------------------------------------------------
    def encode(self, clef_record: dict[str, Any], max_length: int = 4096):
        return self.mod.encode_record(self.tokenizer, clef_record, max_length=max_length)

    @torch.inference_mode()
    def hidden_states(self, encoded: list) -> list[torch.Tensor]:
        """Backbone last hidden state per record, unpadded, on CPU in bf16 (for head-only training)."""
        batch = self.mod.collate_records(encoded, self.tokenizer.pad_token_id, self.device)
        base = self.model.language_model
        text_model = base.model.language_model if hasattr(base.model, "language_model") else base.model
        out = text_model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"],
                         use_cache=False, return_dict=True)
        return [out.last_hidden_state[i, : len(e.input_ids)].to("cpu") for i, e in enumerate(encoded)]

    def output_embedding_weight(self) -> torch.Tensor:
        return self.model.language_model.get_output_embeddings().weight

    # -- scoring -------------------------------------------------------------------------------
    @torch.inference_mode()
    def probabilities(self, clef_records: list[dict[str, Any]]) -> tuple[list[list[float]], list[float]]:
        """Softmax over (allow, block, escalate) per record, and wall-clock seconds per record."""
        probs, secs = [], []
        for rec in clef_records:
            t0 = time.perf_counter()
            enc = self.encode(rec)
            batch = self.mod.collate_records([enc], self.tokenizer.pad_token_id, self.device)
            logits = self.model(batch)[0][0].float()
            if self.device.type == "mps":
                torch.mps.synchronize()
            secs.append(time.perf_counter() - t0)
            probs.append(logits.softmax(-1).tolist())
        return probs, secs
