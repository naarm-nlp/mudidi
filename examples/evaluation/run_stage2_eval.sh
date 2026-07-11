#!/usr/bin/env bash
# Stage 2 MDF eval: Evenki-Russian page_1 (run directory inference first).
#
# Usage (from repo root): bash examples/evaluation/run_stage2_eval.sh

uv run mudidi benchmark evaluate stage2 \
  --config examples/configs/benchmark/stage2-evaluation.yaml \
  "$@"
