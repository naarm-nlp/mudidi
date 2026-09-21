#!/usr/bin/env bash
#
# Move selected page_<N>_lang.json files from annotation/outputs into
# dataset/MUDIDI/dictionaries/<Dictionary>/Stage 1 Gold OCR/page_<N>/.
#
set -euo pipefail
cd "$(dirname "$0")/../.."

OUTPUTS_ROOT="annotation/outputs"
DICTIONARIES_ROOT="dataset/MUDIDI/dictionaries"

# Leave empty to process every dictionary found under annotation/outputs.
DICTIONARIES=(
  # "Assyrian-English"
  # "Bengalese-English"
  # "Canala-English"
  # "Chepang-English"
  # "Chukchi-Russian"
  # "Circassian-English-Turkish"
  # "Efik-English"
  # "Evenki-Russian"
  "Georgian-Russian"
  # "Gojri-English-Hindi"
  # "Greek-English"
  # "Gujarati-English"
  # "Iñupiatun Eskimo-English"
  # "Japanese-English"
  # "Kashmiri-English"
  # "Khmer-English"
  # "Malay-English"
  # "Na-English-Chinese-French"
  # "Nahuatl-French"
  # "Punjabi-English"
  # "Reel-English"
  # "Ritharngu-English"
  # "Sanskrit-English"
  # "Shilluk-English"
  # "Syriac-English"
  # "Telugu-English"
  # "Thai-Russian"
  # "Tiri-English"
  # "Vernacular Syriac-Kurdish_Turkish-English"
  "Yiddish-English"
)

# Leave empty to move every page for the selected dictionaries.
PAGES=()

ARGS=(
  --outputs-root "$OUTPUTS_ROOT"
  --dictionaries-root "$DICTIONARIES_ROOT"
)

if [ "${#DICTIONARIES[@]}" -gt 0 ]; then
  ARGS+=( --dictionaries "${DICTIONARIES[@]}" )
fi

if [ "${#PAGES[@]}" -gt 0 ]; then
  ARGS+=( --pages "${PAGES[@]}" )
fi

# Remove --dry-run above when you are happy with the preview, or add --overwrite
# below if you want destination page_<N>_lang.json files replaced.
# ARGS+=( --overwrite )

uv run python scripts/move_lid_span.py "${ARGS[@]}"
