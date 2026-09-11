#!/bin/bash
# Ablation: which knob makes hard tier hard. Runs sequentially; each run resumes if interrupted.
cd "$(dirname "$0")/.." || exit 1
set -a; source ../.env.local; set +a
run() { echo "=== $(date) $*"; uv run python -m harness.run_eval --workers 4 --out results "$@"; }
run --tier hard-full      --seeds 0-9 --model claude-sonnet-5
run --tier hard-branching --seeds 0-9 --model claude-sonnet-5
run --tier hard-fuzzy     --seeds 0-9 --model claude-sonnet-5
run --tier hard-full      --seeds 0-9 --model claude-haiku-4-5 --no-thinking
echo "=== $(date) ALL DONE"
