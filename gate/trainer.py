"""Module C training: fine-tune the Clef-flash joint decision head on cached backbone states.

The Qwen3.5-9B backbone is frozen. It is run once over every record to cache its last hidden
states (the head's only input from the backbone), then unloaded; the head (122 pretrained
tensors) is trained on the cache with C1 cross-entropy + C2 pairwise RLCD margin loss.
"""
import gc
import json
import random
import resource
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import torch

from common.exceptions import ModelUnavailableError
from gate.clef import ClefGate, load_schema_module, snapshot_dir
from gate.decision import LABEL_TO_ID, LABELS, decision_metrics
from gate.loss import gate_loss
from gate.template import record_to_clef
from gate.tuning import ThresholdTuner


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class _EmbeddingLookup:
    """Indexable stand-in for the backbone's output embedding matrix (the head only gathers rows)."""

    def __init__(self, weight: torch.Tensor):
        self.weight = weight

    def __getitem__(self, ids):
        return self.weight[ids].float()


def _rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9  # macOS reports bytes


class GateTrainer:
    def __init__(self, config: dict[str, Any], run_id: str, results_dir: Path | str = "results"):
        self.config = config
        self.tcfg = config["gate_training"]
        self.run_dir = Path(results_dir) / run_id
        self.cache_dir = self.run_dir / "hidden_cache"
        self.ckpt_dir = self.run_dir / "checkpoints"
        self.device = torch.device("mps")

    # ------------------------------------------------------------------ cache
    def _cache_split(self, gate: ClefGate, name: str, records: list[dict[str, Any]],
                     on_progress: Optional[Callable[[dict[str, Any]], None]]) -> list[dict[str, Any]]:
        path = self.cache_dir / f"{name}.pt"
        if path.exists():
            cached = torch.load(path)
            if [c["id"] for c in cached] == [r["id"] for r in records]:
                return cached
        n = self.config["gate_training"]["history_window_n"]
        enc = [gate.encode(record_to_clef(r, n)) for r in records]
        order = sorted(range(len(records)), key=lambda i: len(enc[i].input_ids))  # batch similar lengths
        out: dict[int, dict[str, Any]] = {}
        bs, t0 = self.tcfg.get("cache_batch_size", 8), time.time()
        for s in range(0, len(order), bs):
            idx = order[s:s + bs]
            hs = gate.hidden_states([enc[i] for i in idx])
            for i, h in zip(idx, hs):
                e = enc[i]
                out[i] = {"id": records[i]["id"], "hidden": h, "input_ids": list(e.input_ids),
                          "questions": [(q.question_id, q.question_type, q.question_span, q.option_spans, q.option_ids)
                                        for q in e.questions]}
            if on_progress and (s // bs) % 10 == 0:
                done = s + len(idx)
                on_progress({"type": "cache", "split": name, "done": done, "total": len(order),
                             "eta_sec": round((len(order) - done) * (time.time() - t0) / max(1, done), 1)})
        cached = [out[i] for i in range(len(records))]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        torch.save(cached, path)
        return cached

    # ------------------------------------------------------------------ head forward on cache
    def _forward(self, mod, head, emb, items: list[dict[str, Any]]) -> torch.Tensor:
        encoded = [mod.EncodedRecord(
            input_ids=tuple(it["input_ids"]), record_id=it["id"],
            questions=tuple(mod.EncodedQuestion(question_id=q[0], question_type=q[1], question_span=tuple(q[2]),
                                                option_spans=tuple(tuple(s) for s in q[3]), option_ids=tuple(q[4]))
                            for q in it["questions"])) for it in items]
        L = max(len(it["input_ids"]) for it in items)
        hidden = torch.zeros(len(items), L, 4096, device=self.device)
        ids = torch.zeros(len(items), L, dtype=torch.long, device=self.device)
        mask = torch.zeros(len(items), L, dtype=torch.long, device=self.device)
        for b, it in enumerate(items):
            n = len(it["input_ids"])
            hidden[b, :n] = it["hidden"].to(self.device).float()
            ids[b, :n] = torch.tensor(it["input_ids"], device=self.device)
            mask[b, :n] = 1
        res = head(hidden, ids, mask, encoded, emb)
        return torch.stack([r[0] for r in res])  # one 'decision' question per record

    @torch.no_grad()
    def predict(self, mod, head, emb, items, bs: int = 16) -> list[list[float]]:
        head.eval()
        out = []
        for s in range(0, len(items), bs):
            out += self._forward(mod, head, emb, items[s:s + bs]).softmax(-1).tolist()
        return out

    # ------------------------------------------------------------------ train
    def train(self, on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        cfg = self.tcfg
        torch.manual_seed(self.config["harness"]["random_seed_base"])
        random.seed(self.config["harness"]["random_seed_base"])
        train_recs = read_jsonl(self.run_dir / "train.jsonl")
        val_recs = read_jsonl(self.run_dir / "val.jsonl")
        if not train_recs or not val_recs:
            raise ModelUnavailableError("train.jsonl/val.jsonl missing or empty: run Module B first.")

        # 1) cache frozen-backbone states, then unload the 9B model (one large model at a time)
        t_cache = time.time()
        gate = ClefGate(device="mps")
        mod = gate.mod
        train_c = self._cache_split(gate, "train", train_recs, on_progress)
        val_c = self._cache_split(gate, "val", val_recs, on_progress)
        emb = _EmbeddingLookup(gate.output_embedding_weight().detach().clone())
        head_cfg = json.loads((snapshot_dir() / "joint_head_config.json").read_text())
        del gate
        gc.collect()
        torch.mps.empty_cache()
        cache_sec = time.time() - t_cache

        # 2) head: pretrained Clef weights, fine-tuned in fp32
        from safetensors.torch import load_file
        head = mod.JointSchemaHead(**head_cfg, dropout=cfg["dropout"])
        head.load_state_dict(load_file(snapshot_dir() / "joint_head.safetensors"), strict=True)
        head = head.to(self.device).float()
        y_train = torch.tensor([LABEL_TO_ID[r["label"]] for r in train_recs], device=self.device)
        y_val = [r["label"] for r in val_recs]

        zero_shot = self.predict(mod, head, emb, val_c)  # epoch 0 = untouched pretrained head
        zs = decision_metrics(y_val, zero_shot, self.config["thresholds"]["tau_allow"], self.config["thresholds"]["tau_block"])

        counts = torch.bincount(y_train, minlength=3).float()
        weights = (counts.sum() / counts.clamp(min=1)).sqrt()
        weights = (weights / weights.mean()).to(self.device)

        pairs_by_id: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(train_recs):
            if r["source"] in ("rlcd", "hard_negative"):  # matched pairs feed the C2 contrast loss
                pairs_by_id[r["pair_id"]].append(i)
        groups = list(pairs_by_id.values()) + [[i] for i, r in enumerate(train_recs) if r["source"] not in ("rlcd", "hard_negative")]

        opt = torch.optim.AdamW(head.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
        bp, accum, epochs = cfg["batch_pairs"], cfg["grad_accum"], cfg["epochs"]
        steps_per_epoch = (len(groups) + bp - 1) // bp
        hist: dict[str, Any] = {"train_loss_curve": [], "val_loss_curve": [], "val_accuracy_curve": [],
                                "val_harmful_pass_through_curve": [], "val_benign_false_blocks_curve": [],
                                "epoch_metrics": [], "checkpoints": []}
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        best_val, best_epoch, t0, gstep = float("inf"), 0, time.time(), 0
        for epoch in range(1, epochs + 1):
            head.train()
            random.shuffle(groups)
            ep_loss, ep_start = 0.0, time.time()
            for s in range(steps_per_epoch):
                batch_groups = groups[s * bp:(s + 1) * bp]
                idx, pairs = [], []
                for g in batch_groups:
                    base = len(idx)
                    idx += g
                    if len(g) == 2:
                        pairs.append((base, base + 1))
                logits = self._forward(mod, head, emb, [train_c[i] for i in idx])
                out = gate_loss(logits, y_train[idx], pairs, cfg["margin"], cfg["lambda_rlcd"], weights)
                (out["total_loss"] / accum).backward()
                if (s + 1) % accum == 0 or s == steps_per_epoch - 1:
                    torch.nn.utils.clip_grad_norm_(head.parameters(), 1.0)
                    opt.step()
                    opt.zero_grad(set_to_none=True)
                gstep += 1
                ep_loss += out["total_loss"].item()
                if on_progress and (s % 5 == 0 or s == steps_per_epoch - 1):
                    el = time.time() - t0
                    sps = gstep / max(el, 1e-9)
                    pt = {"type": "step", "epoch": epoch, "total_epochs": epochs, "step": s + 1,
                          "total_steps": steps_per_epoch, "loss": out["total_loss"].item(),
                          "ce_loss": out["ce_loss"].item(), "margin_loss": float(out["margin_loss"]),
                          "steps_per_sec": round(sps, 2), "eta_sec": (epochs * steps_per_epoch - gstep) / max(sps, 1e-9),
                          "rss_gb": round(_rss_gb(), 1), "mps_gb": round(torch.mps.current_allocated_memory() / 1e9, 1)}
                    on_progress(pt)
                    hist["train_loss_curve"].append({"epoch": epoch, "step": s, "global_step": gstep,
                                                     "total_loss": round(pt["loss"], 4), "ce_loss": round(pt["ce_loss"], 4),
                                                     "margin_loss": round(pt["margin_loss"], 4),
                                                     "steps_per_sec": pt["steps_per_sec"], "eta_sec": round(pt["eta_sec"], 1)})
            # validation
            probs = self.predict(mod, head, emb, val_c)
            yv = torch.tensor([LABEL_TO_ID[l] for l in y_val])
            val_loss = float(torch.nn.functional.nll_loss(torch.log(torch.tensor(probs).clamp_min(1e-9)), yv))
            m = decision_metrics(y_val, probs, self.config["thresholds"]["tau_allow"], self.config["thresholds"]["tau_block"])
            acc3 = sum(max(range(3), key=lambda k: p[k]) == LABEL_TO_ID[l] for p, l in zip(probs, y_val)) / len(probs)
            em = {"epoch": epoch, "train_loss": round(ep_loss / steps_per_epoch, 4), "val_loss": round(val_loss, 4),
                  "val_accuracy": round(acc3, 4), "harmful_pass_through": round(m["harmful_pass_through"], 4),
                  "benign_false_blocks": round(m["benign_false_blocks"], 4), "escalate_rate": round(m["escalate_rate"], 4),
                  "epoch_duration_sec": round(time.time() - ep_start, 1)}
            hist["epoch_metrics"].append(em)
            hist["val_loss_curve"].append({"epoch": epoch, "loss": em["val_loss"]})
            hist["val_accuracy_curve"].append({"epoch": epoch, "accuracy": em["val_accuracy"]})
            hist["val_harmful_pass_through_curve"].append({"epoch": epoch, "rate": em["harmful_pass_through"]})
            hist["val_benign_false_blocks_curve"].append({"epoch": epoch, "rate": em["benign_false_blocks"]})
            ck = self.ckpt_dir / f"checkpoint_epoch_{epoch}.pt"
            torch.save({"head_state": {k: v.cpu() for k, v in head.state_dict().items()}, "epoch": epoch, "metrics": em}, ck)
            hist["checkpoints"].append({"epoch": epoch, "path": str(ck), "filename": ck.name, "val_loss": em["val_loss"],
                                        "val_accuracy": em["val_accuracy"], "harmful_pass_through": em["harmful_pass_through"],
                                        "benign_false_blocks": em["benign_false_blocks"],
                                        "size_mb": round(ck.stat().st_size / 1e6, 2)})
            if em["val_loss"] < best_val:
                best_val, best_epoch = em["val_loss"], epoch
            if on_progress:
                on_progress({"type": "epoch", "epoch": epoch, "total_epochs": epochs, "summary": em})

        # 3) best checkpoint -> freeze thresholds on val (test is never touched here)
        best = torch.load(self.ckpt_dir / f"checkpoint_epoch_{best_epoch}.pt")
        torch.save(best, self.run_dir / "best_checkpoint.pt")
        head.load_state_dict({k: v.to(self.device) for k, v in best["head_state"].items()})
        probs = self.predict(mod, head, emb, val_c)
        tuner = ThresholdTuner(self.config["thresholds"]["target_max_harmful_pass_through"],
                               self.config["thresholds"]["target_max_benign_false_blocks"],
                               self.config["thresholds"]["tau_allow"], self.config["thresholds"]["tau_block"])
        tuning = tuner.evaluate_grid([{"true_label": l, "p_allow": p[0], "p_block": p[1], "p_escalate": p[2]}
                                      for l, p in zip(y_val, probs)])
        tuner.freeze_thresholds(tuning, self.run_dir)
        best_t = tuning["best_thresholds"]
        total = time.time() - t0
        result = {
            "run_id": self.run_dir.name, "device": "mps", "total_epochs": epochs,
            "total_training_time_sec": round(total, 1), "backbone_cache_time_sec": round(cache_sec, 1),
            "best_checkpoint": str(self.run_dir / "best_checkpoint.pt"), "best_epoch": best_epoch,
            "final_epoch": hist["epoch_metrics"][-1], "history": hist,
            "zero_shot_val": zs, "peak_rss_gb": round(_rss_gb(), 1), "n_train": len(train_recs), "n_val": len(val_recs),
            "threshold_tuning": {**best_t}, "config": cfg,
        }
        (self.run_dir / "training_metrics.json").write_text(json.dumps(result, indent=2))
        return result
