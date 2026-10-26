#!/usr/bin/env bash
# Long-running, resumable stages that need only local models: Module A replication, then Module B generation.
# Review (OpenAI, needs a real OPENAI_API_KEY in .env), training and evaluation follow; see README.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results/run_002/logs
./sg harness run --dry-run
./sg harness run --replication 2>&1 | tee -a results/run_002/logs/module_a_replication.log
./sg data generate 2>&1 | tee -a results/run_002/logs/module_b_generate.log
