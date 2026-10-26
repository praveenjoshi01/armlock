#!/usr/bin/env bash
# run_004: fresh Module A on the physics harness, run_003 data relabelled with rules v2.1 + self-labelled hard
# negatives, retrain, Module D, final report, and comparison with run_003. Stops at the first failing stage.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
SRC=${SRC:-run_003}; DST=${DST:-run_004}
mkdir -p results/$DST/logs results/$DST/reports
L=results/$DST/logs
./sg harness run --dry-run --run-id $DST       2>&1 | tee -a $L/module_a_dryrun.log
./sg harness run --replication --run-id $DST   2>&1 | tee -a $L/module_a_replication.log
./sg data hardneg --src $SRC --dst $DST        2>&1 | tee -a $L/hardneg.log
./sg train --run-id $DST                       2>&1 | tee -a $L/train.log
./sg e2e --run-id $DST                         2>&1 | tee -a $L/e2e.log
./sg report --run-id $DST                      2>&1 | tee -a $L/final_report.log
./sg compare --base $SRC --new $DST            2>&1 | tee -a $L/compare.log
for f in harness_report dataset_report training_report eval_report final_report comparison_report; do
  cp results/$DST/$f.html results/$DST/$f.md results/$DST/reports/
done
for r in run_002 run_003; do
  cp results/$r/rescore_report.html results/$DST/reports/${r}_rescore_report.html
  cp results/$r/rescore_report.md results/$DST/reports/${r}_rescore_report.md
done
echo "ALL DONE: $(ls results/$DST/reports)" | tee -a $L/done.out
