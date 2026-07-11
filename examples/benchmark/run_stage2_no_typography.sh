#!/usr/bin/env bash
set -euo pipefail

# Stage 2 no-typography benchmark.
#
# Uses gold Stage 1 OCR with <b>/<i> tags stripped, then runs Stage 2 with:
#   - dictionary intro when an intro folder/file exists for the entry
#   - Toolbox MDF guide PDF
#
# Outputs land under:
#   outputs/benchmark/stage-2-no-typography/<language>/stage-2/<experiment>/<page>/<page>.mdf.txt
#
# The script prepares MUDIDI-compatible Stage 1 input from the dataset layout:
#   dataset/MUDIDI/dictionaries/<language>/Stage 1 Gold OCR/
#     -> outputs/benchmark/stage-2-no-typography/<language>/stage-1/gold_stage1_notypography/

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

DATASET_DIR="${DATASET_DIR:-dataset/MUDIDI/dictionaries}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/benchmark/stage-2-no-typography}"
TOOLBOX_PDF="${TOOLBOX_PDF:-assets/Pages from ToolboxReferenceManual.pdf}"
STAGE2_REASONING="${STAGE2_REASONING:-high}"
BATCH_SIZE="${BATCH_SIZE:-5}"
GOLD_NO_TYPOGRAPHY_SLOT="gold_stage1_notypography"

# Subset of dictionary folders to process across every experiment below.
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

GEMINI_PRO_MODEL="gemini/gemini-3.1-pro-preview"
GPT55_MODEL="openrouter/openai/gpt-5.5"
CLAUDE_OPUS47_MODEL="openrouter/anthropic/claude-opus-4.7"
QWEN3_VL_MODEL="openrouter/qwen/qwen3-vl-235b-a22b-instruct"

find_intro_path() {
    local entry_dir="$1"
    local candidate
    for candidate in \
        "${entry_dir}/introduction" \
        "${entry_dir}/Introduction" \
        "${entry_dir}/intro" \
        "${entry_dir}/Intro" \
        "${entry_dir}/preface" \
        "${entry_dir}/Preface"; do
        if [[ -e "${candidate}" ]]; then
            echo "${candidate}"
            return 0
        fi
    done
    return 1
}

prepare_stage1_no_typography() {
    uv run python - "${DATASET_DIR}" "${OUTPUT_ROOT}" "${GOLD_NO_TYPOGRAPHY_SLOT}" "${LANGUAGES[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

dataset_dir = Path(sys.argv[1])
output_root = Path(sys.argv[2])
plain_slot = sys.argv[3]
languages = sys.argv[4:]
tag_re = re.compile(r"</?(?:b|i)>")

for language in languages:
    src_root = dataset_dir / language / "Stage 1 Gold OCR"
    plain_root = output_root / language / "stage-1" / plain_slot

    if not src_root.is_dir():
        print(f"WARNING: {language}: missing Stage 1 Gold OCR; skipping prep", file=sys.stderr)
        continue

    written = 0
    for gold_flat in sorted(src_root.glob("*/page_*_stage1_GOLD_flat.txt")):
        stem = gold_flat.parent.name
        plain_page_dir = plain_root / stem
        plain_page_dir.mkdir(parents=True, exist_ok=True)

        text = gold_flat.read_text(encoding="utf-8", errors="replace")
        plain_text = tag_re.sub("", text)
        (plain_page_dir / f"{stem}_stage1_flat.txt").write_text(
            plain_text,
            encoding="utf-8",
        )
        written += 1

    print(f"Prepared {language}: {written} no-typography gold Stage 1 page(s).")
PY
}

run_stage2() {
    local model="$1"
    local experiment="$2"

    local language entry_dir pages_dir output_dir config_file intro_path
    local -a intro_args

    for language in "${LANGUAGES[@]}"; do
        entry_dir="${DATASET_DIR}/${language}"
        pages_dir="${entry_dir}/Dictionary pages"
        output_dir="${OUTPUT_ROOT}/${language}"
        config_file="${entry_dir}/dictionary_languages.yaml"
        intro_args=()

        if [[ ! -d "${pages_dir}" ]]; then
            echo "WARNING: ${language}: missing pages dir: ${pages_dir}; skipping." >&2
            continue
        fi
        if [[ ! -f "${config_file}" ]]; then
            echo "WARNING: ${language}: missing dictionary_languages.yaml; skipping." >&2
            continue
        fi
        if intro_path="$(find_intro_path "${entry_dir}")"; then
            intro_args=(--intro "${intro_path}")
        else
            echo "WARNING: ${language}: no intro found in dataset entry; running with Toolbox guide only." >&2
        fi

        echo ""
        echo "------------------------------------------------------------"
        echo " ${language}"
        echo " Stage 1 slot: ${GOLD_NO_TYPOGRAPHY_SLOT}"
        echo " Stage 2 slot: ${experiment}"
        echo " Batch size:   ${BATCH_SIZE}"
        echo " Output dir:   ${output_dir}"
        echo "------------------------------------------------------------"

        if ! uv run python -m mudidi.cli.extract \
            --benchmark \
            --strategy two_stage \
            --stage 2 \
            --stage-2-pass-1-model "${model}" \
            --stage-2-pass-2-model "${model}" \
            --stage2-reasoning "${STAGE2_REASONING}" \
            --batch-size "${BATCH_SIZE}" \
            --pages "${pages_dir}" \
            --output-dir "${output_dir}" \
            --dictionary-languages "${config_file}" \
            "${intro_args[@]}" \
            --toolbox-pdf "${TOOLBOX_PDF}" \
            --one-page-per-entry \
            --stage1-source predictions \
            --stage1-input flat \
            --experiment-name "${GOLD_NO_TYPOGRAPHY_SLOT}" \
            --stage2-experiment-name "${experiment}"; then
            echo "WARNING: ${language}: stage-2 experiment ${experiment} failed or was skipped; continuing." >&2
        fi
    done
}

prepare_stage1_no_typography

# run_stage2 "${GEMINI_PRO_MODEL}" "gemini31pro_${STAGE2_REASONING}_mdf_intro_toolbox_gold_notypography"
run_stage2 "${GPT55_MODEL}" "gpt55_${STAGE2_REASONING}_mdf_intro_toolbox_gold_notypography"
run_stage2 "${CLAUDE_OPUS47_MODEL}" "claudeopus47_${STAGE2_REASONING}_mdf_intro_toolbox_gold_notypography"
# run_stage2 "${QWEN3_VL_MODEL}" "qwen3vl235_${STAGE2_REASONING}_mdf_intro_toolbox_gold_notypography"
