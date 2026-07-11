#!/usr/bin/env bash
# Reproduce the complete 10-language Stage 2 E2E matrix recorded under
# outputs/benchmark/stage-2-e2e. The sweep stages the required
# gemini31pro_flat_alpha prediction slot from outputs/benchmark/stage-1.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage2-e2e-full-sweep.yaml \
  "$@"
