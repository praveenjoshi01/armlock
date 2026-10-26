# Module C — Gate Training Report

**Run ID:** `run_002` · **Date:** 2026-10-22 07:18

> **Training path: Clef-flash decision head (frozen Qwen3.5-9B backbone, head fine-tuned).** No fallback was used.

## 1. CONFIRM check (spec 6.3)

- Interface: Qwen3_5ForConditionalGeneration backbone + JointSchemaHead (joint_head.safetensors) scored by load_release_model()/systemone(); a 'choice' question returns one logit per option.
- Head: `{"hidden_size": 4096, "width": 1024, "routing_layers": 2, "layers": 4, "heads": 16, "feedforward": 4096}`, 122 tensors, loaded with `strict=True`.
- Snapshot: `/Users/praveenjoshi/.cache/huggingface/hub/models--Cloudflare--clef-flash/snapshots/f0b7d3cd65296abfe459e6c228b618f5d8b26d2f`
- MPS available: True; the 9B backbone runs in bf16 on MPS (reference PyTorch kernels for linear attention).
- LoRA: **not used.** The backbone is frozen and only the decision head is trained (optional per spec 6.3); LoRA would need a backbone forward/backward per step and was out of budget on this machine.

## 2. Configuration

- Train records 3,252, validation records 404; history window N = 5
- Epochs 6, batch = 8 pairs, grad-accum 1, lr 5e-05, weight decay 0.01, head dropout 0.1
- Loss: C1 cross-entropy (sqrt-inverse-frequency class weights) + 0.5 × C2 pairwise RLCD margin loss (margin 1.0)
- Memory: Gemma is unloaded before the backbone loads; the backbone is unloaded after its hidden states are cached.

## 3. Validation metrics per epoch

Decision rule at the default thresholds (τ_allow 0.9, τ_block 0.5).

| Epoch | Train loss | Val loss | Val acc (argmax) | Harmful pass-through | Benign false blocks | Escalate rate | Seconds |
|---|---|---|---|---|---|---|---|
| 0 (pretrained head, zero-shot) | – | – | – | 0.00% | 100.00% | 52.23% | – |
| 1 | 0.9349 | 0.5793 | 86.4% | 29.85% | 9.15% | 11.88% | 139.0 |
| 2 | 0.3467 | 0.1667 | 95.5% | 0.00% | 5.23% | 7.92% | 135.2 |
| 3 | 0.2238 | 0.0588 | 98.3% | 0.00% | 2.61% | 8.91% | 135.6 |
| 4 | 0.1581 | 0.0576 | 98.8% | 2.99% | 0.00% | 7.18% | 135.5 |
| 5 | 0.1292 | 0.0413 | 98.5% | 0.00% | 1.31% | 8.91% | 135.7 |
| 6 | 0.058 | 0.102 | 97.5% | 1.49% | 4.25% | 10.89% | 135.6 |

Best epoch by validation loss: **5**.

## 4. Frozen thresholds (tuned on validation only)

- τ_allow = **0.76**, τ_block = **0.2**
- Validation harmful pass-through **0.00%** (target < 1%), benign false blocks **0.65%** (target < 5%), escalate rate 7.92%
- Targets met on validation: **PASS**. Validation is drawn from the training distribution, so these are optimistic; Module D reports the held-out test, decomposed and harness slices.

## 5. Checkpoints

| Epoch | File | Val loss | Size (MB) |
|---|---|---|---|
| 1 | `checkpoint_epoch_1.pt` | 0.5793 | 487.09 |
| 2 | `checkpoint_epoch_2.pt` | 0.1667 | 487.09 |
| 3 | `checkpoint_epoch_3.pt` | 0.0588 | 487.09 |
| 4 | `checkpoint_epoch_4.pt` | 0.0576 | 487.09 |
| 5 | `checkpoint_epoch_5.pt` | 0.0413 | 487.09 |
| 6 | `checkpoint_epoch_6.pt` | 0.102 | 487.09 |
| best | `best_checkpoint.pt` (epoch 5) | | |

## 6. Runtime and hardware

- Machine: macOS-26.6.2-arm64-arm-64bit; device MPS
- Backbone caching: 3534.2 s; training wall time 823.0 s; peak RSS 34.2 GB