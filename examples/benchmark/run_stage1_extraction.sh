#!/usr/bin/env bash
# Reproduce the complete 30-language Stage 1 benchmark matrix recorded under
# outputs/benchmark/stage-1. Pass sweep options such as --dry-run,
# --experiment NAME or --max-runs N through "$@".

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage1-full-sweep.yaml \
  "$@"
