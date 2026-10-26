# Module C — Gate Training Report

**Run ID:** `run_004` · **Date:** 2026-10-23 21:34

> **Training path: Clef-flash decision head (frozen Qwen3.5-9B backbone, head fine-tuned).** No fallback was used.

## 1. CONFIRM check (spec 6.3)

- Interface: Qwen3_5ForConditionalGeneration backbone + JointSchemaHead (joint_head.safetensors) scored by load_release_model()/systemone(); a 'choice' question returns one logit per option.
- Head: `{"hidden_size": 4096, "width": 1024, "routing_layers": 2, "layers": 4, "heads": 16, "feedforward": 4096}`, 122 tensors, loaded with `strict=True`.
- Snapshot: `/Users/praveenjoshi/.cache/huggingface/hub/models--Cloudflare--clef-flash/snapshots/f0b7d3cd65296abfe459e6c228b618f5d8b26d2f`
- MPS available: True; the 9B backbone runs in bf16 on MPS (reference PyTorch kernels for linear attention).
- LoRA: **not used.** The backbone is frozen and only the decision head is trained (optional per spec 6.3); LoRA would need a backbone forward/backward per step and was out of budget on this machine.

## 2. Configuration

- Train records 5,917, validation records 739; history window N = 5
- Epochs 6, batch = 8 pairs, grad-accum 1, lr 5e-05, weight decay 0.01, head dropout 0.1
- Loss: C1 cross-entropy (sqrt-inverse-frequency class weights) + 0.5 × C2 pairwise RLCD margin loss (margin 1.0)
- Memory: Gemma is unloaded before the backbone loads; the backbone is unloaded after its hidden states are cached.

## 3. Validation metrics per epoch

Decision rule at the default thresholds (τ_allow 0.9, τ_block 0.5).

| Epoch | Train loss | Val loss | Val acc (argmax) | Harmful pass-through | Benign false blocks | Escalate rate | Seconds |
|---|---|---|---|---|---|---|---|
| 0 (pretrained head, zero-shot) | – | – | – | 0.00% | 100.00% | 55.48% | – |
| 1 | 0.749 | 0.3082 | 88.2% | 2.55% | 17.12% | 13.26% | 313.4 |
| 2 | 0.2954 | 0.1789 | 94.2% | 0.00% | 11.42% | 11.37% | 308.4 |
| 3 | 0.1719 | 0.2302 | 92.3% | 0.43% | 12.79% | 9.74% | 308.4 |
| 4 | 0.1368 | 0.199 | 95.4% | 2.55% | 3.20% | 8.80% | 308.3 |
| 5 | 0.1059 | 0.1744 | 95.8% | 0.85% | 4.57% | 9.34% | 308.3 |
| 6 | 0.111 | 0.1643 | 97.0% | 1.28% | 3.42% | 9.88% | 308.5 |

Best epoch by validation loss: **6**.

## 4. Frozen thresholds (tuned on validation only)

- τ_allow = **0.999**, τ_block = **0.2**
- Validation harmful pass-through **0.00%** (target < 1%), benign false blocks **10.05%** (target < 5%), escalate rate 13.40%
- Targets met on validation: **FAIL**. Validation is drawn from the training distribution, so these are optimistic; Module D reports the held-out test, decomposed and harness slices.

## 5. Checkpoints

| Epoch | File | Val loss | Size (MB) |
|---|---|---|---|
| 1 | `checkpoint_epoch_1.pt` | 0.3082 | 487.09 |
| 2 | `checkpoint_epoch_2.pt` | 0.1789 | 487.09 |
| 3 | `checkpoint_epoch_3.pt` | 0.2302 | 487.09 |
| 4 | `checkpoint_epoch_4.pt` | 0.199 | 487.09 |
| 5 | `checkpoint_epoch_5.pt` | 0.1744 | 487.09 |
| 6 | `checkpoint_epoch_6.pt` | 0.1643 | 487.09 |
| best | `best_checkpoint.pt` (epoch 6) | | |

## 6. Runtime and hardware

- Machine: macOS-26.6.2-arm64-arm-64bit; device MPS
- Backbone caching: 6885.1 s; training wall time 1867.9 s; peak RSS 28.4 GB