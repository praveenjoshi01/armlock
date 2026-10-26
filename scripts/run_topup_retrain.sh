#!/usr/bin/env bash
# Top up the curated run_002 dataset, retrain on it as run_003, run Module D, final report, and compare gates.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1   # progress reaches the logs immediately (they looked stale when buffered)
SRC=${SRC:-run_002}; DST=${DST:-run_003}; PAIRS=${PAIRS:-1000}
mkdir -p results/$DST/logs results/$DST/reports
L=results/$DST/logs
./sg data topup --src $SRC --dst $DST --pairs $PAIRS 2>&1 | tee -a $L/topup.log
./sg data assemble --src $SRC --dst $DST --review 200 2>&1 | tee -a $L/assemble.log
./sg train --run-id $DST            2>&1 | tee -a $L/train.log
./sg e2e --run-id $DST              2>&1 | tee -a $L/e2e.log
./sg report --run-id $DST           2>&1 | tee -a $L/final_report.log
./sg compare --base $SRC --new $DST 2>&1 | tee -a $L/compare.log
for f in harness_report dataset_report training_report eval_report final_report comparison_report; do
  cp results/$DST/$f.html results/$DST/$f.md results/$DST/reports/
done
cp results/$SRC/curated/curation_report.html results/$SRC/curated/curation_report.md results/$DST/reports/
echo "ALL DONE: $(ls results/$DST/reports)" | tee -a $L/done.out
