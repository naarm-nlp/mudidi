#!/usr/bin/env bash
#
# Expose Label Studio publicly via an ngrok tunnel, then start Label Studio
# with PUBLIC_URL wired up automatically (fixes CSRF on login/save when
# annotators connect from outside your machine).
#
# Prerequisites:
#   brew install ngrok
#   ngrok config add-authtoken <your-token>   # from https://dashboard.ngrok.com
#
# Usage:
#   bash annotation/examples/start_label_studio_public.sh
#   PORT=9000 bash annotation/examples/start_label_studio_public.sh
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-8080}"
NGROK_API="http://127.0.0.1:4040/api/tunnels"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ERROR: ngrok is not installed. Install it with: brew install ngrok" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Start ngrok in the background, tunneling to the Label Studio port.
# ---------------------------------------------------------------------------
echo "Starting ngrok tunnel to localhost:$PORT …"
ngrok http "$PORT" --log=stdout >/tmp/ngrok-mudidi.log 2>&1 &
NGROK_PID=$!

trap 'kill $NGROK_PID 2>/dev/null; exit' INT TERM

# ---------------------------------------------------------------------------
# 2. Poll ngrok's local API until the public URL is assigned.
# ---------------------------------------------------------------------------
echo -n "Waiting for ngrok tunnel"
PUBLIC_URL=""
for i in $(seq 1 30); do
  PUBLIC_URL="$(curl -sf "$NGROK_API" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    tunnels = json.load(sys.stdin).get("tunnels", [])
    https = [t["public_url"] for t in tunnels if t["public_url"].startswith("https://")]
    print(https[0] if https else (tunnels[0]["public_url"] if tunnels else ""))
except Exception:
    print("")' 2>/dev/null || true)"
  if [ -n "$PUBLIC_URL" ]; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo ""
    echo "ERROR: ngrok did not report a public URL within 30 s. Check /tmp/ngrok-mudidi.log" >&2
    kill $NGROK_PID 2>/dev/null
    exit 1
  fi
done

echo "Public URL: $PUBLIC_URL"
echo ""

# ---------------------------------------------------------------------------
# 3. Launch Label Studio with PUBLIC_URL set, keeping ngrok alive alongside it.
# ---------------------------------------------------------------------------
export PORT PUBLIC_URL
PORT="$PORT" PUBLIC_URL="$PUBLIC_URL" \
  bash "$REPO_ROOT/annotation/examples/start_label_studio_local.sh" &
LS_SCRIPT_PID=$!

trap 'kill $NGROK_PID $LS_SCRIPT_PID 2>/dev/null; exit' INT TERM
wait $LS_SCRIPT_PID
kill $NGROK_PID 2>/dev/null || true
