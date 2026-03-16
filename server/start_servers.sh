#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

cleanup() {
  local exit_code=$?
  if [[ -n "${WS_PID:-}" ]]; then kill "$WS_PID" 2>/dev/null || true; fi
  if [[ -n "${AI_PID:-}" ]]; then kill "$AI_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

python server/ws_server.py &
WS_PID=$!

node server/ai_controller.js &
AI_PID=$!

echo "ws_server.py started (pid=$WS_PID)"
echo "ai_controller.js started (pid=$AI_PID)"
echo "Press Ctrl+C to stop both servers"

wait "$WS_PID" "$AI_PID"
