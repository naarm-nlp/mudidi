#!/usr/bin/env bash
# Stage-2 spot check: 10 Stimson pages on Gemini Pro vs Flash with reasoning token logging.
# Reuses Stage-1 transcripts and parse-rules from the full stimson run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PDF="inputs/Stimson 1964- Dictionnaire.pdf"
SOURCE="outputs/stimson-1964-dictionnaire"
PARSE_RULES="$SOURCE/parse-rules.json"
TOOLBOX="assets/Pages from ToolboxReferenceManual.pdf"
PAGES="100,150,200,250,300,350,400,450,500,550"
PAGE_LIST=(100 150 200 250 300 350 400 450 500 550)

setup_dir() {
  local out="$1"
  mkdir -p "$out/stage-1"
  for p in "${PAGE_LIST[@]}"; do
    ln -sfn "$ROOT/$SOURCE/stage-1/page_${p}" "$out/stage-1/page_${p}"
  done
  if [[ -d "$SOURCE/.rendered_snippets" && ! -e "$out/.rendered_snippets" ]]; then
    ln -sfn "$ROOT/$SOURCE/.rendered_snippets" "$out/.rendered_snippets"
  fi
}

run_stage2() {
  local out="$1"
  local model="$2"
  local label="$3"

  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  $label → $out"
  echo "  model=$model  pages=$PAGES"
  echo "════════════════════════════════════════════════════════════"

  uv run python -m mudidi.cli.extract \
    --pages "$PDF" \
    --dict-pages "$PAGES" \
    --output-dir "$out" \
    --stage 2-pass-2 \
    --stage1-mode flat \
    --stage1-input flat \
    --stage1-source predictions \
    --stage-2-pass-2-model "$model" \
    --parse-rules-file "$PARSE_RULES" \
    --toolbox-pdf "$TOOLBOX" \
    --stage2-reasoning high \
    --batch-size 5 \
    --overwrite
}

setup_dir "outputs/stimson-reasoning-spotcheck-pro"
setup_dir "outputs/stimson-reasoning-spotcheck-flash"

run_stage2 \
  "outputs/stimson-reasoning-spotcheck-pro" \
  "gemini/gemini-3.1-pro-preview" \
  "Gemini 3.1 Pro (high reasoning)"

run_stage2 \
  "outputs/stimson-reasoning-spotcheck-flash" \
  "gemini/gemini-3.5-flash" \
  "Gemini 3.5 Flash (high reasoning)"

echo ""
echo "Done. Usage files:"
echo "  outputs/stimson-reasoning-spotcheck-pro/stage-2/page_*/page_*_usage.json"
echo "  outputs/stimson-reasoning-spotcheck-flash/stage-2/page_*/page_*_usage.json"
