#!/usr/bin/env bash
# Stage 1 flat eval: Evenki-Russian page_1 (run directory inference first).
#
# Usage (from repo root): bash examples/evaluation/run_stage1_eval.sh

uv run mudidi benchmark evaluate stage1 \
  --config examples/configs/benchmark/stage1-evaluation.yaml \
  "$@"
