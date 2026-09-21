#!/usr/bin/env bash
#
# Start Label Studio and import all NER language-span projects automatically.
#
# On first run: Label Studio opens in your browser — create an account, then
# visit http://localhost:8080/user/account to copy your API token. The script
# will prompt you for it once and save it to $TOKEN_FILE for future runs.
#
# On subsequent runs: projects are skipped if they already exist (idempotent).
# Pass OVERWRITE=1 to delete and recreate all projects.
#
# Usage:
#   bash annotation/examples/start_label_studio_local.sh
#   PORT=9000 bash annotation/examples/start_label_studio_local.sh
#   OVERWRITE=1 bash annotation/examples/start_label_studio_local.sh
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Preserve a public URL supplied by the tunnel wrapper; explicit environment values
# take precedence over repository .env values.
CALLER_PUBLIC_URL="${PUBLIC_URL:-}"

# Load .env early so PUBLIC_URL and tokens are available before Label Studio starts.
if [ -f "$REPO_ROOT/.env" ]; then
  while IFS='=' read -r key val || [ -n "$key" ]; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    val="${val%\"}"
    val="${val#\"}"
    val="${val%\'}"
    val="${val#\'}"
    case "$key" in
      PUBLIC_URL|LABEL_STUDIO_*|LS_ACCESS_TOKEN|CSRF_TRUSTED_ORIGINS|\
      USE_X_FORWARDED_HOST|USE_X_FORWARDED_PORT|SECURE_PROXY_SSL_HEADER)
        export "$key=$val" 2>/dev/null || true
        ;;
    esac
  done < "$REPO_ROOT/.env"
fi
if [ -n "$CALLER_PUBLIC_URL" ]; then
  export PUBLIC_URL="$CALLER_PUBLIC_URL"
fi

# The parent MUDIDI environment uses DEBUG=release, while Django expects a boolean.
export DEBUG="${LABEL_STUDIO_DEBUG:-false}"

HOST="${HOST:-localhost}"
PORT="${PORT:-8080}"
LS_URL="http://$HOST:$PORT"
OVERWRITE="${OVERWRITE:-0}"

# Public URL when exposed via Cloudflare Tunnel, ngrok, etc. (fixes CSRF on login/save).
PUBLIC_URL="${PUBLIC_URL:-}"
PUBLIC_URL="${PUBLIC_URL%/}"
if [ -n "$PUBLIC_URL" ]; then
  export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-$PUBLIC_URL}"
  export LABEL_STUDIO_HOST="${LABEL_STUDIO_HOST:-$PUBLIC_URL}"
  export USE_X_FORWARDED_HOST="${USE_X_FORWARDED_HOST:-true}"
  export USE_X_FORWARDED_PORT="${USE_X_FORWARDED_PORT:-true}"
  export SECURE_PROXY_SSL_HEADER="${SECURE_PROXY_SSL_HEADER:-HTTP_X_FORWARDED_PROTO,https}"
fi
BROWSER_URL="${PUBLIC_URL:-$LS_URL}"

# Label Studio data dir (SQLite DB + uploads) — kept outside the repo.
export LABEL_STUDIO_BASE_DATA_DIR="${LABEL_STUDIO_BASE_DATA_DIR:-$HOME/.label-studio-mudidi}"
mkdir -p "$LABEL_STUDIO_BASE_DATA_DIR"

# Serve local gold text files so tasks can reference the repo directly.
export LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED="${LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED:-true}"
export LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT="${LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT:-$REPO_ROOT}"

TOKEN_FILE="$LABEL_STUDIO_BASE_DATA_DIR/.token"

# ---------------------------------------------------------------------------
# 1. Start Label Studio in the background.
# ---------------------------------------------------------------------------
if command -v label-studio >/dev/null 2>&1; then
  LS_CMD="label-studio"
else
  LS_CMD="uvx label-studio"
  echo "label-studio not on PATH — using 'uvx label-studio' (downloads on first run)…"
fi

if [ -n "$PUBLIC_URL" ]; then
  echo "Starting Label Studio at $LS_URL (public: $PUBLIC_URL) …"
else
  echo "Starting Label Studio at $LS_URL …"
fi
$LS_CMD start --host "$HOST" --port "$PORT" &
LS_PID=$!

# Ensure we kill LS if the script is interrupted before we exec-wait below.
trap 'kill $LS_PID 2>/dev/null; exit' INT TERM

# ---------------------------------------------------------------------------
# 2. Wait for Label Studio to be ready.
# ---------------------------------------------------------------------------
echo -n "Waiting for Label Studio to be ready"
for i in $(seq 1 60); do
  if curl -sf "$LS_URL/health" >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 2
  if [ "$i" -eq 60 ]; then
    echo ""
    echo "ERROR: Label Studio did not start within 120 s. Check for port conflicts."
    kill $LS_PID 2>/dev/null
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# 3. Resolve the API token (env var > .env > saved file > prompt).
# ---------------------------------------------------------------------------
if [ -n "${LABEL_STUDIO_TOKEN:-}" ]; then
  TOKEN="$LABEL_STUDIO_TOKEN"
  echo "Using token from LABEL_STUDIO_TOKEN env var."
elif [ -n "${LS_ACCESS_TOKEN:-}" ]; then
  TOKEN="$LS_ACCESS_TOKEN"
  echo "Using PAT from LS_ACCESS_TOKEN (.env)."
elif [ -f "$TOKEN_FILE" ]; then
  TOKEN="$(cat "$TOKEN_FILE")"
  echo "Using saved token from $TOKEN_FILE"
else
  echo ""
  echo "─────────────────────────────────────────────────────────"
  echo "  First-time setup: Label Studio needs an API token."
  echo ""
  echo "  1. Open $BROWSER_URL in your browser."
  echo "  2. Create an account (or sign in)."
  echo "  3. Go to $BROWSER_URL/user/account"
  echo "  4. Copy your API Token."
  echo "─────────────────────────────────────────────────────────"
  echo -n "Paste your Label Studio API token here and press Enter: "
  read -r TOKEN
  if [ -z "$TOKEN" ]; then
    echo "No token entered — skipping project import. Projects can be imported later with:"
    echo "  uv run python annotation/label_studio/setup_ner_projects.py --ls-token <token>"
    echo ""
    if [ -n "$PUBLIC_URL" ]; then
      echo "Label Studio local: $LS_URL | public: $PUBLIC_URL — press Ctrl+C to stop."
    else
      echo "Label Studio is running at $LS_URL — press Ctrl+C to stop."
    fi
    wait $LS_PID
    exit 0
  fi
  echo "$TOKEN" > "$TOKEN_FILE"
  echo "Token saved to $TOKEN_FILE (delete this file to re-enter it)."
fi

# ---------------------------------------------------------------------------
# 4. Import all NER projects (idempotent — existing projects are skipped).
# ---------------------------------------------------------------------------
echo ""
overwrite_flag=""
[ "$OVERWRITE" = "1" ] && overwrite_flag="--overwrite"

if ! uv run python annotation/label_studio/setup_ner_projects.py \
  --ls-url "$LS_URL" \
  --ls-token "$TOKEN" \
  --outputs-root "annotation/outputs" \
  --dictionaries-root "dataset/MUDIDI/dictionaries" \
  $overwrite_flag; then
  # Auth likely failed — remove the saved token so next run re-prompts.
  if [ -f "$TOKEN_FILE" ] && [ "$TOKEN" = "$(cat "$TOKEN_FILE")" ]; then
    rm -f "$TOKEN_FILE"
    echo "Removed stale token from $TOKEN_FILE — re-run to enter a fresh token."
  fi
fi

# Keep existing projects synchronized with newly generated local span maps.
uv run python annotation/label_studio/watch_ner_projects.py \
  --ls-url "$LS_URL" \
  --ls-token "$TOKEN" \
  --outputs-root "annotation/outputs" \
  --dictionaries-root "dataset/MUDIDI/dictionaries" \
  --render-root ".label-studio-renders" \
  --document-root "$LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT" &
WATCH_PID=$!
trap 'kill "$WATCH_PID" "$LS_PID" 2>/dev/null; exit' INT TERM

echo ""
if [ -n "$PUBLIC_URL" ]; then
  echo "Label Studio local: $LS_URL"
  echo "Public URL (share with annotators): $PUBLIC_URL"
  echo "Press Ctrl+C to stop."
else
  echo "Label Studio is running at $LS_URL — press Ctrl+C to stop."
fi

# ---------------------------------------------------------------------------
# 5. Keep Label Studio in the foreground.
# ---------------------------------------------------------------------------
wait $LS_PID
kill "$WATCH_PID" 2>/dev/null || true
