#!/usr/bin/env bash
set -euo pipefail

# Stage-2 direct MDF benchmark extraction. Outputs land in:
#   {entry}/outputs/stage-2/<experiment-name>/<stem>/<stem>.mdf.txt
#
# This script targets the current MUDIDI CLI:
#   uv run python -m mudidi.cli.extract --benchmark --stage 2 ...
#
# Stage 2 direct MDF is now the only Stage 2 extraction mode, so the old
# dictionary-extractor flag `--stage2-mode direct_mdf` is no longer used.
# Re-runs without --overwrite resume from cached outputs; pass --overwrite to
# refresh parse-rule discovery and per-page MDF for each experiment.
#
# Stage-1 inputs default to gold in benchmark mode:
#   outputs/stage-1-gold/<stem>/*_stage1_GOLD_flat.txt
#
# Experiment naming:
#   <model>_<reasoning>_mdf_<intro|nointro>_<toolbox|notoolbox>

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

SAMPLES_DIR="${SAMPLES_DIR:-/Users/davidsamuel/Documents/Code/dictionary-extractor/assets/dictionaries/samples}"
EXTRACT_EXTRA_ARGS=("$@")

# Subset of language subfolders to process across every experiment below.
# Edit this list, or remove the --languages line in run_stage2(), to run all samples.
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

TOOLBOX_PDF="${TOOLBOX_PDF:-assets/Pages from ToolboxReferenceManual.pdf}"
STAGE2_REASONING="${STAGE2_REASONING:-high}"
STAGE1_EXPERIMENT="${STAGE1_EXPERIMENT:-}"
STAGE2_EXPERIMENT_SUFFIX="${STAGE2_EXPERIMENT_SUFFIX:-}"

STAGE1_SOURCE_ARGS=()
if [[ -n "${STAGE1_EXPERIMENT}" ]]; then
    STAGE1_SOURCE_ARGS=(
        --stage1-source predictions
        --experiment-name "${STAGE1_EXPERIMENT}"
    )
    if [[ -z "${STAGE2_EXPERIMENT_SUFFIX}" ]]; then
        STAGE2_EXPERIMENT_SUFFIX="from_${STAGE1_EXPERIMENT}"
    fi
fi

GEMINI_PRO_MODEL="gemini/gemini-3.1-pro-preview"
GPT55_MODEL="openrouter/openai/gpt-5.5"
CLAUDE_OPUS47_MODEL="openrouter/anthropic/claude-opus-4.7"
QWEN3_VL_MODEL="openrouter/qwen/qwen3-vl-235b-a22b-instruct"

run_stage2() {
    local model="$1"
    local experiment="$2"
    shift 2
    if [[ -n "${STAGE2_EXPERIMENT_SUFFIX}" ]]; then
        experiment="${experiment}_${STAGE2_EXPERIMENT_SUFFIX}"
    fi

    if ! uv run python -m mudidi.cli.extract \
        --benchmark \
        --strategy two_stage \
        --stage 2 \
        --stage-2-pass-1-model "${model}" \
        --stage-2-pass-2-model "${model}" \
        --stage2-reasoning "${STAGE2_REASONING}" \
        --samples-dir "${SAMPLES_DIR}" \
        --languages "${LANGUAGES[@]}" \
        --one-page-per-entry \
        --stage1-input flat \
        "${STAGE1_SOURCE_ARGS[@]}" \
        --stage2-experiment-name "${experiment}" \
        "$@" \
        "${EXTRACT_EXTRA_ARGS[@]}"; then
        echo "WARNING: stage-2 experiment ${experiment} failed or was skipped; continuing." >&2
    fi
}

run_stage2_matrix() {
    local model="$1"
    local slug="$2"

    run_stage2 "${model}" "${slug}_${STAGE2_REASONING}_mdf_intro_notoolbox"
    run_stage2 "${model}" "${slug}_${STAGE2_REASONING}_mdf_intro_toolbox" \
        --toolbox-pdf "${TOOLBOX_PDF}"
    run_stage2 "${model}" "${slug}_${STAGE2_REASONING}_mdf_nointro_notoolbox" \
        --no-intro
    run_stage2 "${model}" "${slug}_${STAGE2_REASONING}_mdf_nointro_toolbox" \
        --no-intro \
        --toolbox-pdf "${TOOLBOX_PDF}"
}

run_stage2_matrix "${GEMINI_PRO_MODEL}" "gemini31pro"
run_stage2_matrix "${GPT55_MODEL}" "gpt55"
run_stage2_matrix "${CLAUDE_OPUS47_MODEL}" "claudeopus47"
run_stage2_matrix "${QWEN3_VL_MODEL}" "qwen3vl235"
