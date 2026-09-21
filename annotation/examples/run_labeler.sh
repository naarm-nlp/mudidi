#!/usr/bin/env bash
#
# Run the script span-map labeler over dictionaries and write *_lang.json under
# annotation/outputs/<dictionary>/.
#
#   llm (default)    — LLM Language-Script tagger (tier2_labeler.py)
#   script           — deterministic Unicode script identification only
#
# Edit the variables below, then run:
#   bash annotation/examples/run_labeler.sh
#
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root, so paths below are repo-relative

# ----------------------------------------------------------------------------
# Config — edit these directly.
# ----------------------------------------------------------------------------
INPUT_DIR="dataset/MUDIDI/dictionaries"
OUTPUT_ROOT="annotation/outputs"

# Labeler mode: llm (default, Language-Script via tier2_labeler) or script (deterministic).
LABELER_MODE="llm"

# LLM settings — only used when LABELER_MODE=llm.
MODEL="gemini/gemini-3.1-pro-preview"
REASONING_EFFORT="high"
DRIFT_GATE=0.02
TEMPERATURE=0.2
BATCH_SIZE=1
STAGE="all"

RULES=()
# RULES+=( "dataset/MUDIDI/dictionaries/Evenki-Russian/language_rules.yaml" )

# Keep existing span maps; label only pages without a *_lang.json.
OVERWRITE=0

# Dictionaries to process (folder names under INPUT_DIR). Empty = all with gold pages.
DICTIONARIES=(
  "Yiddish-English"
)

# Newly synced gold pages that do not yet have span maps. The page filter is
# applied to each selected dictionary; nonexistent page numbers are ignored.
PAGES=(
  2
  37
  41
  303
)
# ----------------------------------------------------------------------------

script_args=( --dictionaries-root "$INPUT_DIR" --output-root "$OUTPUT_ROOT" )
llm_args=( --dictionaries-root "$INPUT_DIR" --output-root "$OUTPUT_ROOT"
           --model "$MODEL" --reasoning-effort "$REASONING_EFFORT"
           --temperature "$TEMPERATURE" --max-drift "$DRIFT_GATE"
           --batch-size "$BATCH_SIZE" --stage "$STAGE" )
[ "$OVERWRITE" = "1" ] && script_args+=( --overwrite ) && llm_args+=( --overwrite )
if [ "${#RULES[@]}" -gt 0 ]; then
  for rules_file in "${RULES[@]}"; do
    [ -n "$rules_file" ] || continue
    llm_args+=( --rules "$rules_file" )
  done
fi
if [ "${#DICTIONARIES[@]}" -gt 0 ]; then
  for d in "${DICTIONARIES[@]}"; do
    [ -n "$d" ] || continue
    script_args+=( --dictionary "$d" )
    llm_args+=( --dictionary "$d" )
  done
  echo "Processing dictionaries: ${DICTIONARIES[*]}"
else
  echo "Processing ALL dictionaries with gold pages"
fi
if [ "${#PAGES[@]}" -gt 0 ]; then
  llm_args+=( --pages "${PAGES[@]}" )
  echo "Pages (LLM only): ${PAGES[*]}"
fi
echo "Input : $INPUT_DIR"
echo "Output: $OUTPUT_ROOT/<dictionary>/page_<N>_lang.json"
echo

if [ "$LABELER_MODE" = "script" ]; then
  echo ">>> Script labeler (deterministic Unicode classification)"
  uv run python annotation/labelers/script_labeler.py "${script_args[@]}"
else
  echo ">>> LLM Language-Script labeler (stage=$STAGE, model=$MODEL)"
  uv run python annotation/labelers/tier2_labeler.py "${llm_args[@]}"
fi
