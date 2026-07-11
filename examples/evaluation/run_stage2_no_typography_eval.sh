#!/usr/bin/env bash
set -euo pipefail

# Evaluate the Stage 2 no-typography benchmark and compare it with the
# existing Gemini 3.1 Pro intro+toolbox Stage 2 MDF baseline.
#
# Usage:
#   bash examples/evaluation/run_stage2_no_typography_eval.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

EXPERIMENT_NAME="${EXPERIMENT_NAME:-gemini31pro_high_mdf_intro_toolbox_gold_notypography}"
BASELINE_EXPERIMENT="${BASELINE_EXPERIMENT:-gemini31pro_high_mdf_intro_toolbox}"
PRED_ROOT="${PRED_ROOT:-outputs/benchmark/stage-2-no-typography}"
DATASET_DIR="${DATASET_DIR:-dataset/MUDIDI/dictionaries}"
BASELINE_SUMMARY="${BASELINE_SUMMARY:-evaluations/stage2_mdf_eval/stage2_mdf_eval_summary.csv}"
OUTPUT_DIR="${OUTPUT_DIR:-evaluations/stage2_mdf_eval_no_typography}"
COMPARISON_OUTPUT="${COMPARISON_OUTPUT:-${OUTPUT_DIR}/stage2_mdf_no_typography_vs_baseline.csv}"

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

uv run mudidi benchmark evaluate stage2 \
    --dataset-dir "${DATASET_DIR}" \
    --pred-root "${PRED_ROOT}" \
    --experiment-name "${EXPERIMENT_NAME}" \
    --languages "${LANGUAGES[@]}" \
    --baseline-summary "${BASELINE_SUMMARY}" \
    --baseline-experiment "${BASELINE_EXPERIMENT}" \
    --comparison-output "${COMPARISON_OUTPUT}" \
    -o "${OUTPUT_DIR}"
