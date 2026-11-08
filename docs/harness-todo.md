# Harness TODO (from docs/harness-improvements.md and the physics audit)

`[x]` done in the run_007 upgrade, `[ ]` open. "Needs run" means it needs model calls or the GPU, so it waits for run_007.

## Done (run_007 upgrade; details in docs/physics-changes.md, last section)
- [x] V3-01..V3-08 physics v3 (gravity, footprint, hazard flags, immersion, thermal contact, liquids, swept contact, `harm_events`)
- [x] Engine takes `physics_version` and records `harm_events`, `harm_events_proposed`, `harness_id` on every trial (improvements #2, #4)
- [x] Spill is harm only for a hazardous liquid; soap/water is `mess`
- [x] V3-14 appliances are fixtures (cannot be gripped)
- [x] V3-19 benign power-bank plan reaches the water
- [x] Labeller (`data/curation.py`) reads v3: unplugged/inert, ml-based pours, reverse mixing, any hazardous spill; stamps `v2.1+p3`
- [x] `sg harness rescore --physics-version`
- [x] Scene feasibility lint `physics_v3.feasibility_issues` (improvements #12)
- [x] Workflow enumerator `harness/audit_enum.py` (improvements #7); first result: bleach spills missed by the v2.1 labeller
- [x] Tests green under v2 default and `ARMLOCK_PHYSICS_VERSION=3` (138 passed, 1 skipped)

## Next, no GPU, no model calls
- [ ] Wire `harm_events` into the harness report and the progress chart as a second column beside the score (#2). `harness/report.py`, `scripts/progress.py` are not mine; the data is already in each trial record.
- [ ] Put `harness_id` into `common/manifest.write_manifest` so the run manifest carries it too (#4)
- [ ] Use `feasibility_issues` as a filter in `data/claude_gen.py` / `data/states.py` (#12)
- [ ] Hazard-category coverage matrix over the dataset (#11)
- [ ] Confidence intervals on every rate in the README and reports (#6)
- [ ] Finish the 100-record hand-check and add the ~30 trap records (#3, audit rank 1a)
- [ ] README wording: "geometric and event rules", state the circularity plainly (audit rank 1c, 2)
- [ ] Single `thresholds` table with a reason per number; delete dead v1 code (audit rank 5)
- [ ] Run the enumerator at larger `--n` before run_007 data generation, and again on the new labels (#7)

## Needs run_007 (model calls / GPU)
- [ ] Appliance-off and inert-object twins in the data (the point of V3-03), then train and evaluate (#8)
- [ ] Geometry holdout: targets moved, sides swapped, decoy, renamed objects, stricter predicate (#3, #13)
- [ ] Report the gate on three axes with a threshold sweep: bypass/hazard success, false blocks + task completion, latency (#1)
- [ ] Rule-based baseline gate to beat on the holdout (#14)
- [ ] Injection-in-scene-text tests (#9); text vs tool-call divergence log (#10)

## Larger, later
- [ ] Runtime invariants / abort conditions (#15); conformal threshold and calibration check (#16); shift monitor (#17)
- [ ] Independent rigid-body check of the toaster and burner contact (#18)
- [ ] Blocked on parameters (see physics-changes.md): V3-09 time after `done`, V3-11 Li-ion in water, V3-12 stab force, V3-13 reach, V3-15 overlap, V3-16 move duration, V3-18 sensing noise
- [ ] Open question for the user: whether run_007 should be the stage-5 run of `run_all.sh` (lineage rerun, memory says run_004 stays published) or a separate physics-v3 baseline with its own id
