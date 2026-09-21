#!/usr/bin/env bash
#
# Create / update Label Studio NER projects for script-labeled dictionaries.
# Uses the same dictionary list as annotation/examples/run_labeler.sh.
#
set -euo pipefail
cd "$(dirname "$0")/../.."

OUTPUT_ROOT="annotation/outputs"

# Keep in sync with run_labeler.sh SKIP_DICTIONARIES.
DICTIONARIES=(
    "Georgian-Russian"
    "Yiddish-English"
)

if [ "${#DICTIONARIES[@]}" -eq 0 ]; then
  echo "No dictionaries to set up under $OUTPUT_ROOT" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source .env
set +a

echo "Setting up Label Studio projects for: ${DICTIONARIES[*]}"
uv run python annotation/label_studio/setup_ner_projects.py \
  --ls-url "${LABEL_STUDIO_URL:-http://localhost:8080}" \
  --ls-token "$LS_ACCESS_TOKEN" \
  --dictionaries "${DICTIONARIES[@]}" \
  --overwrite
