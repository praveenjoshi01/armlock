#!/usr/bin/env bash
# Waits for phase 1 (A replication + B generation), then runs review -> split -> train -> e2e -> report
# and gathers every report into results/<run>/reports/. Stops at the first failing stage (no fallbacks).
set -euo pipefail
cd "$(dirname "$0")/.."
RUN=run_002
LOG=results/$RUN/logs
mkdir -p "$LOG" results/$RUN/reports
while pgrep -f scripts/run_phase1.sh >/dev/null; do sleep 60; done
test -s results/$RUN/dataset_raw.jsonl || { echo "phase 1 did not finish (no dataset_raw.jsonl); aborting" | tee -a $LOG/phase2.out; exit 1; }
./sg data review      2>&1 | tee -a $LOG/module_b_review.log
./sg data split       2>&1 | tee -a $LOG/module_b_split.log
./sg train            2>&1 | tee -a $LOG/module_c_train.log
./sg eval             2>&1 | tee -a $LOG/module_d_eval.log
./sg e2e              2>&1 | tee -a $LOG/module_d_e2e.log
./sg report           2>&1 | tee -a $LOG/final_report.log
for f in harness_report dataset_report training_report eval_report final_report; do
  cp results/$RUN/$f.html results/$RUN/$f.md results/$RUN/reports/
done
cp results/$RUN/report.md results/$RUN/reports/
echo "ALL STAGES COMPLETE: $(ls results/$RUN/reports)" | tee -a $LOG/phase2.out
