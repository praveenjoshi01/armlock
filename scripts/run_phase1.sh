#!/usr/bin/env bash
# Long-running, resumable stages that need only local models: Module A replication, then Module B generation.
# Review (OpenAI, needs a real OPENAI_API_KEY in .env), training and evaluation follow; see README.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN=${RUN:-run_002}
mkdir -p results/$RUN/logs
./sg harness run --dry-run --run-id $RUN
./sg harness run --replication --run-id $RUN 2>&1 | tee -a results/$RUN/logs/module_a_replication.log
./sg data generate --generator ${GENERATOR:-gemma} --run-id $RUN 2>&1 | tee -a results/$RUN/logs/module_b_generate.log
