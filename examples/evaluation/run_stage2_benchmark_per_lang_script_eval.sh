#!/usr/bin/env bash
set -euo pipefail

# Batch Stage 2 benchmark eval with per-language-script field-value metrics.
#
# This evaluates oracle/gold-Stage-1 Stage 2 benchmark outputs:
#   outputs/benchmark/stage-2/<dictionary>/stage-2/<experiment>/<page>/<page>.mdf.txt
#
# Or, with E2E=1, end-to-end Stage 2 benchmark outputs:
#   outputs/benchmark/stage-2-e2e/<dictionary>/stage-2/<experiment>/<page>/<page>.mdf.txt
#
# Per-language-script attribution is read from the projection JSON files written
# beside each gold Stage 2 MDF page:
#   Stage 2 MDF file/page_N/page_N_mdf_lang_projection.json
#
# Usage:
#   bash examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh
#   RUN_PROJECTION=0 bash examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh
#   E2E=1 bash examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh
#   EXPERIMENT_NAME=gemini31pro_high_mdf_intro_toolbox \
#     bash examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

DATASET_DIR="${DATASET_DIR:-${PROJECT_ROOT}/dataset/MUDIDI/dictionaries}"
E2E="${E2E:-0}"
if [[ "${E2E}" == "1" || "${E2E}" == "true" || "${E2E}" == "yes" ]]; then
    DEFAULT_PRED_ROOT="${PROJECT_ROOT}/outputs/benchmark/stage-2-e2e"
    DEFAULT_OUTPUT_DIR="${PROJECT_ROOT}/evaluations/stage2_mdf_eval_e2e"
    DEFAULT_RUN_PROJECTION=0
else
    DEFAULT_PRED_ROOT="${PROJECT_ROOT}/outputs/benchmark/stage-2"
    DEFAULT_OUTPUT_DIR="${PROJECT_ROOT}/evaluations/stage2_mdf_per_lang-script_eval"
    DEFAULT_RUN_PROJECTION=1
fi
PRED_ROOT="${PRED_ROOT:-${DEFAULT_PRED_ROOT}}"
OUTPUT_DIR="${OUTPUT_DIR:-${DEFAULT_OUTPUT_DIR}}"
PROJECTION_OUTPUT_DIR="${PROJECTION_OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/stage2_mdf_stage1_lang_projection}"
RUN_PROJECTION="${RUN_PROJECTION:-${DEFAULT_RUN_PROJECTION}}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-}"

if [[ "${RUN_PROJECTION}" == "1" ]]; then
    echo "Refreshing Stage 1 -> Stage 2 language-script projection JSONs"
    OUTPUT_DIR="${PROJECTION_OUTPUT_DIR}" \
        bash examples/evaluation/run_stage2_mdf_stage1_lang_projection.sh
    echo ""
fi

EVAL_ARGS=(
    --dataset-dir "${DATASET_DIR}"
    --pred-root "${PRED_ROOT}"
    -o "${OUTPUT_DIR}"
)

if [[ -n "${EXPERIMENT_NAME}" ]]; then
    EVAL_ARGS+=(--experiment-name "${EXPERIMENT_NAME}")
else
    EVAL_ARGS+=(--all-experiments)
fi

uv run mudidi benchmark evaluate stage2 "${EVAL_ARGS[@]}" "$@"

echo ""
echo "Summary:                      ${OUTPUT_DIR}/stage2_mdf_eval_summary.csv"
echo "Per-language-script summary:  ${OUTPUT_DIR}/stage2_mdf_eval_per_language_script_summary.csv"
echo "Per-language-script detailed: ${OUTPUT_DIR}/stage2_mdf_eval_per_language_script_detailed.csv"
