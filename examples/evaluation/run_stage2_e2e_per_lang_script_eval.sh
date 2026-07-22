#!/usr/bin/env bash
set -euo pipefail

# Evaluate end-to-end Stage 2 benchmark outputs with per-language/script
# field-value metrics. This is the dedicated E2E entry point for the generic
# Stage 2 per-language/script evaluator.
#
# Usage:
#   bash examples/evaluation/run_stage2_e2e_per_lang_script_eval.sh
#   EXPERIMENT_NAME=gemini31pro_high_mdf_intro_toolbox_from_gemini31pro_flat_alpha \
#     bash examples/evaluation/run_stage2_e2e_per_lang_script_eval.sh
#
# Optional environment overrides:
#   PRED_ROOT, DATASET_DIR, OUTPUT_DIR, EXPERIMENT_NAME, RUN_PROJECTION
# Extra arguments are forwarded to `mudidi benchmark evaluate stage2`.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

E2E="1"
RUN_PROJECTION="${RUN_PROJECTION:-0}"
PRED_ROOT="${PRED_ROOT:-${PROJECT_ROOT}/outputs/benchmark/stage-2-e2e}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/stage2_mdf_eval_e2e}"

export E2E RUN_PROJECTION PRED_ROOT OUTPUT_DIR

exec bash "${PROJECT_ROOT}/examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh" "$@"
