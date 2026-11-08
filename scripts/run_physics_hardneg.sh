#!/usr/bin/env bash
# run_004: fresh Module A on the physics harness, run_003 data relabelled with rules v2.1 + self-labelled hard
# negatives, retrain, Module D, final report, and comparison with run_003. Stops at the first failing stage.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
SRC=${SRC:-run_003}; DST=${DST:-run_004}
# Physics version for this run's harness, labelling and hard negatives: 3 (opt-in, see docs/physics-changes.md) unless
# PHYSICS=2 is set. Version 3 needs its own fresh ungated baseline, which the replication step below produces.
export ARMLOCK_PHYSICS_VERSION=${PHYSICS:-3}
mkdir -p results/$DST/logs results/$DST/reports
L=results/$DST/logs
./sg harness run --dry-run --run-id $DST       2>&1 | tee -a $L/module_a_dryrun.log
./sg harness run --replication --run-id $DST   2>&1 | tee -a $L/module_a_replication.log
./sg data hardneg --src $SRC --dst $DST --recheck $([[ "${REVIEWER:-openai}" == opus ]] && echo opus || echo none) 2>&1 | tee -a $L/hardneg.log
./sg train --run-id $DST                       2>&1 | tee -a $L/train.log
./sg e2e --run-id $DST                         2>&1 | tee -a $L/e2e.log
./sg report --run-id $DST                      2>&1 | tee -a $L/final_report.log
./sg compare --base $SRC --new $DST            2>&1 | tee -a $L/compare.log
for f in harness_report dataset_report training_report eval_report final_report comparison_report; do
  cp results/$DST/$f.html results/$DST/$f.md results/$DST/reports/
done
# Physics re-score of the earlier lineage runs (replays their recorded trials; no model calls). RESCORE lists the
# run ids; the default is the original lineage. A run that already has a rescore_report is not re-scored.
for r in ${RESCORE:-run_002 run_003}; do
  [[ -s results/$r/rescore_report.md ]] || ./sg harness rescore --run-id $r 2>&1 | tee -a $L/rescore_$r.log
  cp results/$r/rescore_report.html results/$DST/reports/${r}_rescore_report.html
  cp results/$r/rescore_report.md results/$DST/reports/${r}_rescore_report.md
done
echo "ALL DONE: $(ls results/$DST/reports)" | tee -a $L/done.out
