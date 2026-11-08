#!/usr/bin/env bash
# One command to rerun the whole armlock experiment, from nothing to the final reports.
#
#   ./scripts/run_all.sh            run every stage (resumable: finished trials and pairs are skipped)
#   ./scripts/run_all.sh --plan     print the stages and what each needs, run nothing
#   FROM=3 ./scripts/run_all.sh     start at stage 3 (use after a stage failed and you fixed the cause)
#   GENERATOR=claude REVIEWER=opus ./scripts/run_all.sh
#                                   Claude (the Claude Code session) writes the RLCD pairs instead of Gemma. The run pauses
#                                   with exit code 3 when pairs are open; ask Claude to "generate and continue", then rerun.
#
# Lineage (see README): run_002 = first real run, run_003 = labels audited and topped up, run_004 = physics + hard
# negatives. Override the run ids with R1/R2/R3 to produce a fresh lineage without touching existing results.
# REVIEWER=opus replaces the OpenAI review with Claude Opus subagents (50% of pairs + every risky allow, no spend).
# Needs: Ollama with qwen3.8:27b-mlx and gemma3:27b, Cloudflare/clef-flash in the Hugging Face cache,
# OPENAI_API_KEY in .env (review step only). About 25-30 hours of compute on a 64 GB Apple-silicon Mac.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
export GENERATOR=${GENERATOR:-gemma} REVIEWER=${REVIEWER:-openai}
R1=${R1:-run_002}; R2=${R2:-run_003}; R3=${R3:-run_004}; FROM=${FROM:-1}

STAGES=(
  "1|Module A replication, Gemma pair generation (run $R1)|RUN=$R1 scripts/run_phase1.sh"
  "2|Review, split, train, evaluate, report (run $R1)|RUN=$R1 scripts/run_phase2.sh"
  "3|Audit the labels, write the corrected dataset ($R1/curated)|./sg data curate --run-id $R1"
  "4|Top up, retrain, evaluate, compare (run $R2)|SRC=$R1 DST=$R2 scripts/run_topup_retrain.sh"
  "5|Physics harness, hard negatives, retrain, evaluate (run $R3)|SRC=$R2 DST=$R3 RESCORE=\"$R1 $R2\" scripts/run_physics_hardneg.sh"
  "6|Gate vs free checks: head-to-head and implicit-hazard tests ($R3)|./sg headtohead --run-id $R3 && ./sg implicit --run-id $R3"
  "7|Verbatim failure log ($R3)|.venv/bin/python scripts/build_failure_log.py $R3"
  "8|Winning criteria check and progress chart ($R3)|.venv/bin/python scripts/progress.py add $R3 --label \"${ITER_LABEL:-run_all lineage}\""
)

if [[ "${1:-}" == "--plan" ]]; then
  for s in "${STAGES[@]}"; do IFS='|' read -r n d c <<<"$s"; printf '%s. %s\n     %s\n' "$n" "$d" "$c"; done
  exit 0
fi

preflight() {
  local models; models=$(ollama list)   # captured first: "ollama list | grep -q" can fail under pipefail (SIGPIPE)
  grep -q "qwen3.8:27b-mlx" <<<"$models" || { echo "missing Ollama model qwen3.8:27b-mlx"; exit 1; }
  # gemma3:27b is the pair generator (and the judge baseline in evaluation), so it is needed in every mode
  grep -q "gemma3:27b"      <<<"$models" || { echo "missing Ollama model gemma3:27b"; exit 1; }
  .venv/bin/python -c "from gate.clef import snapshot_dir; snapshot_dir()" 2>/dev/null \
    || { echo "Cloudflare/clef-flash is not in the Hugging Face cache"; exit 1; }
  if [[ "$REVIEWER" == "openai" ]]; then
    grep -q '^OPENAI_API_KEY=.\+' .env 2>/dev/null && ! grep -q 'dummy-key' .env \
      || { echo "put a real OPENAI_API_KEY in .env (REVIEWER=openai needs it; REVIEWER=opus does not)"; exit 1; }
  fi
}
preflight

for s in "${STAGES[@]}"; do
  IFS='|' read -r n d c <<<"$s"
  (( n < FROM )) && continue
  echo "=== stage $n: $d"
  rc=0; bash -c "$c" || rc=$?
  if (( rc == 3 )); then
    echo "=== PAUSED at stage $n: waiting for Claude (exit code 3). Ask Claude to generate the open items, then rerun with FROM=$n."
    exit 3
  elif (( rc != 0 )); then
    exit $rc
  fi
done
echo "ALL STAGES COMPLETE. Reports: results/$R3/reports/  Failure log: results/$R3/failure_cases.jsonl  Progress chart: results/progress/progress.png"
