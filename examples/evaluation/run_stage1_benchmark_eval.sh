#!/usr/bin/env bash
set -euo pipefail

# Batch Stage 1 flat eval against MUDIDI dataset gold using migrated benchmark preds.
#
# Requires: scripts/migrate_legacy_outputs.py (outputs/benchmark/stage-1/...)
#
# Usage:
#   bash examples/evaluation/run_stage1_benchmark_eval.sh
#   STAGE1_OUTPUT_SUBDIR=stage-1-ocr bash examples/evaluation/run_stage1_benchmark_eval.sh
#   bash examples/evaluation/run_stage1_benchmark_eval.sh --include-vlm-ocr

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

DATASET_DIR="${DATASET_DIR:-${PROJECT_ROOT}/dataset/MUDIDI/dictionaries}"
PRED_ROOT="${PRED_ROOT:-${PROJECT_ROOT}/outputs/benchmark/stage-1}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/stage1_flat_eval_benchmark}"
STAGE1_OUTPUT_SUBDIR="${STAGE1_OUTPUT_SUBDIR:-stage-1}"

EVAL_ARGS=(
    --dataset-dir "${DATASET_DIR}"
    --pred-root "${PRED_ROOT}"
    --stage1-output-subdir "${STAGE1_OUTPUT_SUBDIR}"
    -o "${OUTPUT_DIR}"
    --include-vlm-ocr
    "$@"
)

uv run mudidi benchmark evaluate stage1 "${EVAL_ARGS[@]}"

echo ""
echo "Summary: ${OUTPUT_DIR}/stage1_flat_eval_summary.csv"
