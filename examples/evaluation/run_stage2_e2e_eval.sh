#!/usr/bin/env bash
set -euo pipefail

# Evaluate end-to-end Stage 2 benchmark outputs (predicted Stage 1 → Stage 2).
#
# For migrated oracle Stage 2 runs (gold Stage 1), use:
#   PRED_ROOT=outputs/benchmark/stage-2 bash examples/evaluation/run_stage2_e2e_eval.sh
#
# Usage:
#   bash examples/evaluation/run_stage2_e2e_eval.sh
#   EXPERIMENT_NAME=gemini31pro_high_mdf_intro_toolbox_from_gemini31pro_flat_alpha \
#     bash examples/evaluation/run_stage2_e2e_eval.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

PRED_ROOT="${PRED_ROOT:-${PROJECT_ROOT}/outputs/benchmark/stage-2-e2e}"
DATASET_DIR="${DATASET_DIR:-${PROJECT_ROOT}/dataset/MUDIDI/dictionaries}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/stage2_mdf_eval_e2e}"
STAGE1_EXPERIMENT="${STAGE1_EXPERIMENT:-gemini31pro_flat_alpha}"
EXPERIMENT_SUFFIX="${EXPERIMENT_SUFFIX:-from_${STAGE1_EXPERIMENT}}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-gemini31pro_high_mdf_intro_toolbox_${EXPERIMENT_SUFFIX}}"

LANGUAGES=(
    Evenki-Russian
    Chukchi-Russian
    Nahuatl-French
    Na-English-Chinese-French
    Kashmiri-English
    Tiri-English
    Greek-English
    Efik-English
    Circassian-English-Turkish
    "Iñupiatun Eskimo-English"
)

EVAL_ARGS=(
    --dataset-dir "${DATASET_DIR}"
    --pred-root "${PRED_ROOT}"
    --languages "${LANGUAGES[@]}"
    -o "${OUTPUT_DIR}"
)

if [[ -n "${EXPERIMENT_NAME}" ]]; then
    EVAL_ARGS+=(--experiment-name "${EXPERIMENT_NAME}")
else
    EVAL_ARGS+=(--all-experiments)
fi

uv run mudidi benchmark evaluate stage2 "${EVAL_ARGS[@]}"

echo ""
echo "Summary: ${OUTPUT_DIR}/stage2_mdf_eval_summary.csv"
