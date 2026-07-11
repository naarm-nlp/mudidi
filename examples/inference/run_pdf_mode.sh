#!/usr/bin/env bash
# PDF mode on a full dictionary scan (requires pdftk).
#
# Place your downloaded dictionary PDF under inputs/ and adjust --dict-pages,
# --intro-pages, and --parse-rules-page for your volume. This example uses the
# Carolinian-English dictionary bundled at inputs/Carolinian-English-Dictionary.pdf.
#
# Usage (from repo root): bash examples/inference/run_pdf_mode.sh

uv run mudidi run \
  --config examples/configs/production/pdf-inference.yaml \
  "$@"
