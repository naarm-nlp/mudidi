#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

# Batch Stage 1 flat benchmark eval with per-language-script metrics.
#
# This produces the normal Stage 1 benchmark CSVs plus:
#   stage1_flat_eval_per_language_script_detailed.csv
#   stage1_flat_eval_per_language_script_summary.csv
#
# The per-language-script evaluator reads page_<N>_lang.json files co-located with
# the Stage 1 gold flat text, so this script first copies the reviewed span maps
# from annotation/outputs into the dataset page folders.
#
# Usage:
#   bash examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh
#   WORKERS=8 bash examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh
#   SYNC_LID_SPANS=0 bash examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh
#   STAGE1_OUTPUT_SUBDIR=stage-1-ocr bash examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh
#
# Optional: restrict to a subset by editing LANGUAGES below.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

DATASET_DIR="${DATASET_DIR:-${PROJECT_ROOT}/dataset/MUDIDI/dictionaries}"
PRED_ROOT="${PRED_ROOT:-${PROJECT_ROOT}/outputs/benchmark/stage-1}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/stage1_flat_per_lang-script_eval}"
STAGE1_OUTPUT_SUBDIR="${STAGE1_OUTPUT_SUBDIR:-stage-1}"
LID_SPAN_ROOT="${LID_SPAN_ROOT:-${PROJECT_ROOT}/annotation/outputs}"
SYNC_LID_SPANS="${SYNC_LID_SPANS:-1}"
LID_SPAN_OVERWRITE="${LID_SPAN_OVERWRITE:-1}"
WORKERS="${WORKERS:-1}"

# Leave empty to evaluate every dictionary discovered under PRED_ROOT/DATASET_DIR.
LANGUAGES=()

if [[ "${SYNC_LID_SPANS}" == "1" ]]; then
    copied=0
    skipped=0
    missing=0
    echo "Syncing LID span maps from ${LID_SPAN_ROOT} into ${DATASET_DIR}"

    dict_dirs=()
    if [[ "${#LANGUAGES[@]}" -gt 0 ]]; then
        for language in "${LANGUAGES[@]}"; do
            dict_dirs+=( "${LID_SPAN_ROOT}/${language}" )
        done
    else
        dict_dirs=( "${LID_SPAN_ROOT}"/* )
    fi

    for dict_dir in "${dict_dirs[@]}"; do
        [[ -d "${dict_dir}" ]] || continue
        dictionary="$(basename "${dict_dir}")"
        for lang_json in "${dict_dir}"/page_*_lang.json; do
            filename="$(basename "${lang_json}")"
            page="${filename#page_}"
            page="${page%_lang.json}"
            dest_dir="${DATASET_DIR}/${dictionary}/Stage 1 Gold OCR/page_${page}"
            dest="${dest_dir}/${filename}"

            if [[ ! -d "${dest_dir}" ]]; then
                echo "WARNING: missing gold page folder, skipping ${dictionary} page_${page}: ${dest_dir}" >&2
                missing=$((missing + 1))
                continue
            fi
            if [[ -e "${dest}" && "${LID_SPAN_OVERWRITE}" != "1" ]]; then
                skipped=$((skipped + 1))
                continue
            fi
            cp "${lang_json}" "${dest}"
            copied=$((copied + 1))
        done
    done
    echo "LID span sync: copied=${copied}, skipped_existing=${skipped}, missing_page_dirs=${missing}"
    echo ""
fi

EVAL_ARGS=(
    --dataset-dir "${DATASET_DIR}"
    --pred-root "${PRED_ROOT}"
    --stage1-output-subdir "${STAGE1_OUTPUT_SUBDIR}"
    -o "${OUTPUT_DIR}"
    --include-vlm-ocr
    --per-language-script
    --workers "${WORKERS}"
    "$@"
)

if [[ "${#LANGUAGES[@]}" -gt 0 ]]; then
    EVAL_ARGS+=( --languages "${LANGUAGES[@]}" )
fi

uv run mudidi benchmark evaluate stage1 "${EVAL_ARGS[@]}"

echo ""
echo "General summary:       ${OUTPUT_DIR}/stage1_flat_eval_summary.csv"
echo "Per-language summary:  ${OUTPUT_DIR}/stage1_flat_eval_per_language_script_summary.csv"
echo "Per-language detailed: ${OUTPUT_DIR}/stage1_flat_eval_per_language_script_detailed.csv"
