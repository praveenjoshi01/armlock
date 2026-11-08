# Harness improvements drawn from the Sim Atlas literature

Companion to [physics-audit.md](physics-audit.md) (what is wrong), [physics-changes.md](physics-changes.md) (physics v3) and [sim-knowledge-graph.md](sim-knowledge-graph.md) (the graph). This file turns the literature into a ranked to-do list for the **harness and the evaluation around it**, not only the physics.

How it was made: I searched the 71.6k-paper graph (`research/simgraph/query.py search ...`) for each weakness the audit found, then read the **abstracts** of the closest papers. I did not read any paper in full. A paper id below means "its abstract says this"; anything marked *(mine)* is my inference, not the paper's claim. Costs are my rough estimates for one person who knows the repo.

## Bottom line

1. The biggest robustness gap is **evaluation validity**, not physics: one predicate scores, labels and trains (audit 3.1). The literature on guardrail benchmarks scores the gate on **separate axes against a simulator outcome** and treats the test suite itself as something to audit. Do that first.
2. The second gap is **test coverage**: five scenarios, hand-built twins. Papers in the same space use 8 hazard categories / 2,000+ tasks (`2504.14650`) or enumerate tool-call workflows automatically and find ~20% residual unsafe behaviour that benchmarks missed (`2603.18245`). An automatic enumerator over our own tool set needs no model calls.
3. The third is **gate design**: the closest published system (`2604.05427`, SafeGate) pairs a pre-execution gate with a deterministic rule layer and runtime invariants. A rule-based baseline tells us whether the learned gate adds anything.

## Tier 1: make the numbers trustworthy (mostly free, no GPU)

| # | Improvement | Evidence | In this repo | Cost |
|---|---|---|---|---|
| 1 | **Report the gate on three axes, as a curve.** Defence (bypass rate, hazard success in the simulator), usability (false-block rate on safe instructions, task completion on safe), efficiency (latency added). Show a threshold sweep, not one point; the paper finds trade-offs and no guardrail dominating. | `2610.06122` abstract | We report harmful pass-through and benign false blocks. *Not verified:* whether gate latency is recorded in `results/`. | 0.5 day, no compute if scores are logged |
| 2 | **Two-column scoring: `completed` and `harm_events`.** Keep the headline unchanged; add contact, spill, mixing, hold-under-water, hold-on-burner beside it. | Same paper's "hazard success rate in the simulator" is event-based *(mine: mapping)* | Implemented in v3 as `ScoringEngine.harm_events`; the replay shows 0 of 900 scores change, so adding the column costs nothing. Needs wiring in the report. | 3-5 h |
| 3 | **Independent ground truth.** Finish the ~100-record hand-check; add the ~30 trap records from audit 3.4; report agreement with the rule. Then the geometry holdout: targets moved, sides swapped, one decoy, names rewritten, scored by a stricter footprint predicate. | `2604.05427` validates in a simulator *and* real robot runs (separate from its benchmark tasks); `2603.20672`, `2209.09337` frame the simulator as a model with a quantified gap to a reference | Audit rank 1; still open | hand-check 2-4 h; holdout 1 day + one gate run |
| 4 | **Stamp every run with an identity manifest** (model files and hashes, `PHYSICS_VERSION`, `label_rule`, scenario hash, commit). Output-only scoring cannot see a changed stack. | `2610.21135`: tampered deployments pass output-only evaluators at 88-94%; a manifest hash detects them with zero samples | `engine.py` records `physics_version`; the rest is not stamped *(not verified: what run_005 stamps)* | 2-3 h |
| 5 | **Do not treat a stronger LLM judge as ground truth.** The Claude-generates, Opus-reviews loop (`data/claude_review.py`) is a filter, not a label oracle. Keep the deterministic scorer plus the human hand-check as the anchor and report reviewer-vs-human agreement. | `2610.21135` ("stronger judges do not see more"); `2603.18245` | Parent's area; advice only | 0.5 day |
| 6 | **Confidence intervals and multiple-comparison care** on every rate in the README (n is small for 5 scenarios x seeds). | `2602.16943` applies Bonferroni across 18 comparisons; `2505.02274` is titled "statistical foundation in scenario-based testing" (title only, not read) | Not verified whether run reports show CIs | 2 h |

## Tier 2: broaden and attack the test set (cheap to moderate)

| # | Improvement | Evidence | In this repo | Cost |
|---|---|---|---|---|
| 7 | **Automatic workflow enumerator ("test the testers").** Generate valid tool-call sequences over the engine (random plus systematic), run each through `harm_events` and through `label_v2`, and list sequences that harm but are labelled `allow`. The sweep already found 11 flipped labels this way. | `2603.18245`: enumeration finds >20% residual unsafe behaviour; coverage grows with budget | Uses `harness/robot.py`, `physics_v3.py`, `data/curation.py`; no model calls | 1 day |
| 8 | **Metamorphic twins: invariance and sensitivity.** Task-preserving rewrites (rename, reorder, paraphrase) must keep the gate's decision; a one-word state change ("unplugged", "empty", "plastic") must flip it. Test multi-constraint scenes too: getting each atom right did not imply getting the composition right. | `2609.05260` (decision invariance / sensitivity; compositional failures) | Audit ranks 2 and 4 (appliance-off twins, same-geometry different-name twins); v3 flags make the physics side ready | 1 day + regeneration (compute) |
| 9 | **Indirect injection through the scene text.** The gate reads scene descriptions; put instructions or safety claims in object names ("safe, approved") and check the gate ignores them. | `2511.16347`: embodied agents trust instructions placed in the environment | Gate inputs not reviewed in the audit (section 6) | 0.5 day + eval |
| 10 | **Text-vs-action divergence check.** Log cases where the policy's text refuses while its tool calls still execute the action, and make sure the gate scores the **tool calls**, not the prose. | `2602.16943`: such divergence persisted in all six models even under safety prompts | *Not verified* which the gate sees | 0.5 day |
| 11 | **Hazard-category coverage matrix.** Tag each record fire / sharp / electrical / chemical / thermal / crush / fall and show counts. Five scenarios cover about five categories; benchmarks in this area cover eight (`2504.14650`) or ten failure modes (`2412.14470`). Add categories only where a physics rule exists. | `2504.14650`, `2412.14470` | New small script; data is in `results/` | 0.5 day |
| 12 | **Scene feasibility lint for generated data.** Reject generated scenes with overlapping objects, unreachable targets, impossible starts (audit item 7). Use the v3 helpers (`contains_xy`, `top_z`). | LLM-generated environments are an active scaling route (Eurekaverse `2411.01775`), but the abstracts I read did not say how they check physical validity | `data/claude_gen.py` is the parent's | 0.5 day |
| 13 | **Randomise geometry and thresholds, then hold out ranges.** Draw target positions, sizes and water volumes from ranges; train on some, test on disjoint ranges. | `1906.01728` (BayesSim), `1904.04762` (Active DR): domain randomisation as the standard gap-closer | Same as the geometry holdout in #3; this generalises it | 1-2 days + compute |

## Tier 3: stronger gate design and bigger bets

| # | Improvement | Evidence | In this repo | Cost |
|---|---|---|---|---|
| 14 | **Deterministic rule-gate baseline.** Extract hazard properties and apply a fixed decision (SafeGate uses ISO 13482-derived properties, a deterministic gate and an SMT check). Compare the learned gate to it on the holdout. If the learned gate does not beat the rules on unseen geometry, the learning adds only vocabulary. | `2604.05427`; `2309.09919` (safety chip, temporal-logic constraints) | New module; reuses `physics_v3.hazard_ok` as the property check *(mine)* | 2-3 days |
| 15 | **Runtime invariants and abort conditions.** Beyond the pre-execution decision, monitor state each step (a contact, a mixing event, water depth over a limit) and abort. Gives a second layer and measures "ungated harm that a monitor would have stopped". | `2604.05427` (Task Safety Contracts: invariants, guards, abort conditions); `2508.00500` (ProbGuard, proactive prediction rather than reactive rules) | `harm_events` is the building block | 3-4 days |
| 16 | **Calibration and a conformal threshold.** If the gate outputs a score, check calibration and set the block threshold on a calibration split so the miss rate is bounded at a chosen level. Caveat: the guarantee needs calibration and test data to be exchangeable, which the geometry holdout deliberately breaks, so report both. Calibration of each module does not guarantee calibration of the pipeline, so test policy plus gate end to end. | `2604.26479` (calibration checks), `2609.23731` (calibration does not compose), `2505.09427` (conformal prediction for LLM navigation) | *Not verified:* what the gate emits | 2 days |
| 17 | **Distribution-shift monitor on the gate's inputs.** Flag scenes unlike the training set at run time. | `2407.21748` (streaming shift diagnosis with martingales) | Not present | 2-3 days |
| 18 | **A second, independent physics check on two scenarios.** Re-implement the toaster contact and burner contact in a rigid-body engine (MuJoCo/PyBullet are in the graph's industry layer) and compare to the kinematic verdict. Liquid scenarios cannot use it; check those against closed-form volume and area instead. This is the only check not written by the same author as the scorer. | `2209.09337`, `2603.20672` (quantify the gap to a higher-fidelity reference) | New; physics-changes V3-10..V3-19 list the fidelity gaps | 3-5 days |
| 19 | **Evaluation framework hygiene.** Specify each physical eval as a reusable, versioned object with automated setup and termination. | `2610.06306` (Inspect Robots) describes this; *I did not look at its code* | Parent's `scripts/` and `cli/` | open-ended |

## Suggested order

1. Free this week: #2 (wire `harm_events`), #4 (manifest), #6 (CIs), #3 hand-check, README wording from the audit.
2. Next experiment (one GPU run): #8 twins + #3 holdout + #13 ranges, evaluated with the #1 three-axis report and compared with the #14 rule baseline.
3. Then #7 enumerator and #11 coverage matrix to find what is still missing; #15 and #16 only if the gate will be used beyond this benchmark.

## What the literature does **not** give us

* No paper I found validates a gate against a text-only kinematic simulator, so there is no standard to copy for #3; the nearest are the sim-to-real gap papers.
* Burn-injury thresholds, battery-in-water behaviour and chemical-mixing quantities returned off-topic papers (contact-dynamics, plasma). Those parameters still come from the standards and hazard sources listed in `physics-changes.md`, not from the graph.
* The graph holds abstracts only; effect sizes quoted above are the papers' own claims and I have not reproduced any of them.

## Not verified

Whether run reports include latency or confidence intervals; what the gate takes as input (scene text vs tool calls) and whether it outputs a score; what `run_005` stamps in its state file; the contents of the ten papers I read beyond their abstracts. Arxiv ids beyond those whose abstracts I read (`2411.01775`, `2505.02274`) are cited from titles in the graph.
