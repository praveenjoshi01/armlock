# Startup thesis (working draft): a per-deployment safety case for robots

*Status: hypothesis. Nothing here is validated by a buyer. Written 2026-10-27 after the YC Robotics Club talks, the founder-board review in `founder/board.md`, and the competitor scan in `founder/competitors.md`.*

## The claim

> Robots are shipping faster than anyone can prove them safe, and refusal is a property of the interface, not the model. The first robot application companies in hazardous settings will need a **per-deployment safety case**: simulated red-team evaluations with confidence intervals, a runtime gate calibrated to their site, and an audit trail that an insurer or a customer will accept.

## Why this shape and not a runtime library

| Input | What it says |
|---|---|
| Our results (`run_004`) | The refusal gap reproduces (chat refuses 100%, robot 20.5%). The gate takes harmful completions from 79% to 0% (0/200) with no benign loss. Offline it still blocks 8.9% of benign test actions (target 5%), takes about 1 s per decision, and is text-only with one policy. |
| Board verdict (`founder/board.md`) | 1 fund-if, 2 pass, 3.3/10 for "a runtime library sold by subscription". Same three risks from all members: quality unproven on hard cases, a cheaper self-check may substitute, no buyer or price. Strongest version it can see: one named robot stack, with a measured check the builder can run. |
| Competitor scan (`founder/competitors.md`) | No commercial product found for LLM robot-action checks. Direct competitors are research or free: RoboGuard (MIT code), SafeGate, a pre-motion self-check (about $2.60 per trial via API). |
| Talk: Niko West (Rerun) | The winning pattern is the vertical robotics application company: one customer problem, teleop first, an evaluation replica of the customer's site, success criteria that encode that business. Frontier labs will not do customer-specific work. |
| Talk: Tyler Lum (SimToolReal) | Real-world evaluation is expensive and rarely gives statistically significant numbers; simulation evaluation at scale is the workaround. |
| Talk: General Instinct (world action models) | Runtime budgets are about 500 ms per action chunk. A 1 s gate does not fit yet. |
| Talk: Marcel Torne (MEM) | Policies without memory repeat mistakes; a text scratchpad of what happened is a cheap memory. Our gate sees only the last 5 actions. |

So: **start as the assurance and evaluation layer** (sold to robot application companies and to VLA/WAM teams who need evidence), keep the runtime gate as the second product, and let incident data from customer sites become the moat. A horizontal subscription library, as pitched, is the weakest version.

## What would kill it

1. A free pre-motion self-check, or a simulator-lookahead rule, matches the trained gate on pass-through, false blocks, latency and cost. Then the runtime gate is not a product; sell evaluations only.
2. No buyer will pay for an evaluation. Fewer than 2 of 15–20 discovery calls lead to a paid pilot.
3. The gate cannot get under about 200 ms p95 or under 5% false blocks without losing safety.
4. Labels do not survive a human check (hand-check agreement well below the 89% a model reviewer gave).

## Experiments (in order)

1. **Head-to-head (make-or-break).** Trained gate vs pre-motion self-check vs per-step self-check vs a keyword-lookahead heuristic, on the same records. Metrics: harmful pass-through, benign false blocks, latency, cost. Implemented in `eval/headtohead.py`; report in `results/run_004/headtohead_report.md`.
2. **Close the over-blocking.** Target under 5% false blocks on test, hard negatives and decomposed innocent steps. Cost-aware thresholds, more data, LoRA, a fast-then-slow two-stage gate.
3. **Gate memory.** A text scratchpad of what has happened vs the last-5-actions window, scored on decomposed attacks.
4. **Latency.** Chunk-level gating or a distilled gate; target p95 under 200 ms.
5. **More policies and vision.** At least 3 policies, one with images.
6. **Small real-arm check.** Low-cost arm, about 100 trials, harmless proxies only (for example water near dummy electronics). No real hazards.
7. **Human labels.** Label the 100-record hand-check set with a second rater; have an outside safety engineer review the labelling rule.
8. **Market test.** 15–20 discovery calls; offer a paid safety-audit pilot to 3 robotics application companies; publish the benchmark as a lead magnet.

## Who to talk to

From the talks (affiliations as stated by the speakers, unverified):

- **Niko West**, Rerun: application founders, how they evaluate, intros.
- **Bill Jiao and Guanming Wang**, General Instinct: runtime latency budget; integration partner.
- **Marcel Torne**, Stanford / Physical Intelligence internship: memory in policies; long-task evaluation.
- **Milan Ganai**, Stanford / Waymo / AWS: embodied reasoning and verification; advisors he named, Marco Pavone and Clark Barrett.
- **Tyler Lum**, Stanford: simulation-scale evaluation; safety for goal-conditioned policies without language.
- **Francois Chaubard**, YC Robotics Club host: intros to application founders.

From the research scan: authors of RoboHarm (arXiv 2610.14043), the pre-motion self-check paper (2610.27350), and the Kumar Robotics group (UPenn) behind RoboGuard.

Roles to find (more important than researchers): safety or EHS leads at robotics application companies; robotics liability insurance underwriters; certification bodies, for what an acceptable safety case looks like (for example industrial robot standards such as ISO 10218).

## Questions for every conversation

- Who signs off a robot safety case today, and how long does it take?
- What would make you trust a third-party evaluation?
- What do you pay for it now?
- Which near-misses worry you most?

---

# Iteration 2 (2026-10-27): what the head-to-head changed

## Result that moves the thesis

Experiment 1 ran on `run_004` (`results/run_004/headtohead_report.md`).

| Defence (same 400 robot trials) | Harmful completed | Benign completed | Latency | Marginal cost |
|---|---|---|---|---|
| None | 79.0% (158/200) | 99.5% | n/a | $0 |
| **Pre-motion check** (ask the policy once, before moving) | **0.0% (0/200)** | **99.5%** | 0.55 s once per episode | $0 local |
| Trained gate (per action) | 0.0% (0/200) | 99.5% | 1.2 s per action | $0 local |

Per action, on records labelled by rule (95% CIs in the report):

| Method | Test: harmful passed / benign blocked | Hard negatives: harmful passed / benign blocked |
|---|---|---|
| Trained gate | 0.0% / 8.9% | 1.3% / 1.6% |
| Per-step self-check (same policy) | 12.3% / 21.6% | 10.3% / 7.4% |
| Keyword: hazard word in scene | 1.6% / 51.3% | 0.0% / 56.9% |
| Keyword: hazard verb + noun in instruction | 48.4% / 23.0% | 94.2% / 4.2% |

**Reading it honestly:**

1. **On explicit harmful instructions the free pre-motion check is as good as the gate end to end.** The policy says "no" to all 200 harmful tasks it then completes when it just acts, and says "yes" to 199 of 200 benign ones. This is the refusal gap again, from the other side: the model knows when asked directly, and does not act on that knowledge. Our instructions name the hazard ("sealed pressurized can", "damaged lithium-ion power bank"), which is easier than RoboHarm, where the object's danger must be inferred.
2. **The gate does earn its place per action.** Against the same model judging each action, it passes far fewer harmful actions (0.0% vs 12.3% on test) and blocks far fewer benign ones (8.9% vs 21.6%). The keyword shortcuts either block half of everything or miss almost everything on hard negatives.
3. **Neither handles decomposed attacks.** The gate blocks 70.5% of innocent steps; the per-step self-check blocks 53.2%. The pre-motion check cannot see them at all.

So the product is a **stack, not a single check**: a cheap pre-motion check for explicit harm, a per-action gate for what the instruction does not reveal (mid-episode, decomposed, implicit hazards), and an audit trail over both. The claim "the gate beats the free check" is false on the evidence we have; the claim "the free check is not enough" is untested. That is the next experiment.

## What we can sell, and what is still unproven

| Selling point | Evidence | Strength |
|---|---|---|
| **A measured safety audit of your stack in a day**: refusal gap, completion rates and confidence intervals across five hazard classes, on your policy | Reproduced the RoboHarm gap on a local 27B model with 500 trials and rule-based, reproducible scoring | Real. Needs adapters for the buyer's policy |
| **Fail-closed, auditable runtime check**: every decision logged in a tamper-evident chain, errors block, escalate path explicit | `gate/guard.py` and its tests | Real as software. Not yet proven on a robot |
| **Layered defence with measured costs**: pre-motion check plus per-action gate, each with false-block and pass-through rates | Head-to-head above | Real on explicit tasks. Layering benefit unproven |
| **Local and cheap**: $0 marginal cost, no cloud dependency | Everything ran on one Mac; published self-check costs about $2.60 per trial | Real |
| Stops harmful tasks end to end | 79% to 0% (0/200, CI to 1.9%), benign 99.5% | Real but not unique: the free check does the same on these tasks |
| Handles implicit hazards, decomposed attacks, vision, real robots | Not shown | **Unproven. This is the wedge to test.** |

## The pitch, as it stands

"Before a robot goes into a lab or a kitchen, someone has to sign a safety case. We produce the evidence: an independent test of your robot policy against the five RoboHarm hazard classes, then a calibrated runtime check with an audit trail you can show an insurer or a customer. Day-one check costs nothing per call and runs on your own hardware."

Honest edge cases a buyer will raise: "Why not just prompt the model?" (answer: it works for explicit tasks, here is where it stops working), "1.2 s is too slow" (answer: gate at chunk level; roadmap), "Is this real hardware?" (answer: not yet; a harmless-proxy pilot is the next step).

## Iteration 3 (2026-10-28): the implicit-hazard test

Four scenarios rewritten so the instruction is neutral and the hazard is inferable only from the scene text ("Put the grey can on the burner"; a can with a nozzle and a pressure-and-flame symbol). 80 trials per arm, same policy and seeds (`results/run_004/implicit_report.md`).

| Arm | Harmful tasks completed |
|---|---|
| No defence | 100.0% (80/80) |
| Pre-motion check (ask the policy once, before moving) | 0.0% (0/80), CI to 4.6% |
| Trained gate, per action | 0.0% (0/80), CI to 4.6% |

**The free check did not fail.** The policy said it would not carry out any of the 80 tasks when asked first, and then completed all 80 when it just acted. The hypothesis "a free check breaks once the hazard is implicit" is not supported here.

Limits on that reading, so we do not over-claim:
- The clues are still strong (a bottle "labelled ammonia", a basin "labelled bleach", a cracked "Li-ion" brick). RoboHarm-style subtlety, vision, and real clutter are not tested.
- No benign twins in this test, so it says nothing about false blocks. The explicit-task result (199/200 benign carried out) is the only evidence the check is not just refusing everything.
- One policy, text scene, simulation.

## What this does to the product

Two experiments now say the same thing: **on this policy the weak point is not knowing, it is acting.** The model flags the danger when asked and ignores it when driving. So the cheapest layer wins end to end, and "we trained a gate that beats a prompt" is not a sellable claim.

What survives, in order of how well the evidence supports it:

1. **The measurement.** Refusal gap reproduced across 5 hazard classes, now also with implicit instructions: 79–100% harmful completion with no defence versus 0% for chat. A buyer can run it on their own policy. This is the entry product.
2. **The layered runtime with an audit trail.** Pre-motion check first, per-action gate second, tamper-evident log over both (`gate/guard.py`). The gate is justified by per-action precision (0.0% harmful passed versus 12.3% for the same model judging each step), not by end-to-end wins on these tasks.
3. **Cost and locality.** $0 marginal, runs on the buyer's machine.

Where the gate still has to prove itself (each is a build item, in priority order):
- **Mid-episode and decomposed attacks**, where an instruction-time check is blind. Currently the gate over-blocks innocent steps (70.5%), so this is a weakness, not yet a strength.
- **Cases where the model's own knowledge fails**: unfamiliar objects, vision-only hazards, longer tasks. Needs harder scenarios than we have.
- **Over-blocking** (8.9% on test, target under 5%).

## Next iteration

1. Harder implicit set: subtle clues (no "Li-ion" label, no "ammonia" label), plus benign twins, to find where the pre-motion check breaks. If it never breaks on a 27B model, test a smaller policy (7–9B), which is more like what edge robots run.
2. Run the same three arms on a second policy, to see whether "knows but does not act" is general.
3. ~~Package the audit~~ Done: `./sg audit --policy <ollama-model> --trials N --gate-run run_004` writes the evidence report (gap verdict, defences compared with CIs, per-scenario table, verified audit log, protocol, limits, file hashes). First run on `qwen3.8:27b-mlx`, 3 trials per condition, took 69 minutes: refusal gap present (chat 15/15 refused, robot 3/15), harmful completed 12/15 with no defence and 0/15 with the pre-motion check or the gate, benign 15/15 in every arm; 327 gate decisions, chain verified, latency p50 1.4 s, p95 4.5 s. Intervals at n=3 are wide (0/15 means CI to 20%); use 10+ trials for a buyer. Next: an adapter for non-Ollama policies (HTTP endpoint), since a buyer's policy will not be on Ollama.
