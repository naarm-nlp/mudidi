#!/usr/bin/env bash
set -euo pipefail

# Generate descriptive statistics for the canonical MUDIDI dictionary dataset.
#
# Usage:
#   bash examples/evaluation/run_statistics.sh
#   OUTPUT_DIR=/tmp/mudidi-statistics bash examples/evaluation/run_statistics.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

DATASET_DIR="${DATASET_DIR:-${PROJECT_ROOT}/dataset/MUDIDI/dictionaries}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/evaluations/statistics}"

uv run python scripts/generate_dataset_statistics.py \
    --dictionaries-dir "${DATASET_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    "$@"

echo ""
echo "Comprehensive JSON: ${OUTPUT_DIR}/dictionary_statistics.json"
echo "Detailed CSV:       ${OUTPUT_DIR}/dictionary_statistics_per_language_script_detailed.csv"
echo "Summary CSV:        ${OUTPUT_DIR}/dictionary_statistics_summary.csv"
