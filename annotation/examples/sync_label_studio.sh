cd "$(dirname "${BASH_SOURCE[0]}")/../.."
set -a; source .env; set +a

# Preview what would change (always do this first):
# uv run python annotation/label_studio/sync_from_label_studio.py \
#   --ls-url http://localhost:8083 --ls-token "$LS_ACCESS_TOKEN" --dry-run

# Actually write the corrections:
uv run python annotation/label_studio/sync_from_label_studio.py \
  --ls-url http://localhost:8080 --ls-token "$LS_ACCESS_TOKEN"

# # Limit to specific dictionaries:
# uv run python annotation/label_studio/sync_from_label_studio.py \
#   --ls-url http://localhost:8083 --ls-token "$LS_ACCESS_TOKEN" \
#   --dictionaries Assyrian-English Canala-English
