#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

set -a
if [[ -f .env ]]; then
  . ./.env
fi
if [[ -f local.env ]]; then
  . ./local.env
fi
set +a

WS_PORT_VALUE="${WS_PORT:-8081}"
AI_PORT_VALUE="${AI_PORT:-8082}"

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "$PYTHON_BIN"
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi

  return 1
}

is_port_listening() {
  local port="$1"

  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" | tail -n +2 | grep -q .
    return $?
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN -Pn >/dev/null 2>&1
    return $?
  fi

  return 1
}

cleanup() {
  local exit_code=$?
  if [[ -n "${WS_PID:-}" ]]; then kill "$WS_PID" 2>/dev/null || true; fi
  if [[ -n "${AI_PID:-}" ]]; then kill "$AI_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

if ! PYTHON_BIN_VALUE="$(resolve_python_bin)"; then
  echo "[start_servers] error: python/python3 not found."
  exit 1
fi

if is_port_listening "$WS_PORT_VALUE"; then
  echo "[start_servers] ws port $WS_PORT_VALUE already in use, skip starting ws_server.py"
else
  "$PYTHON_BIN_VALUE" server/ws_server.py &
  WS_PID=$!
fi

if is_port_listening "$AI_PORT_VALUE"; then
  echo "[start_servers] ai port $AI_PORT_VALUE already in use, skip starting ai_controller.js"
else
  node server/ai_controller.js &
  AI_PID=$!
fi

if [[ -n "${WS_PID:-}" ]]; then
  echo "ws_server.py started (pid=$WS_PID, port=$WS_PORT_VALUE)"
fi

if [[ -n "${AI_PID:-}" ]]; then
  echo "ai_controller.js started (pid=$AI_PID, port=$AI_PORT_VALUE)"
fi

if [[ -z "${WS_PID:-}" && -z "${AI_PID:-}" ]]; then
  echo "[start_servers] both services are already running, nothing to start"
  exit 0
fi

echo "Press Ctrl+C to stop started services"

PIDS=()
if [[ -n "${WS_PID:-}" ]]; then PIDS+=("$WS_PID"); fi
if [[ -n "${AI_PID:-}" ]]; then PIDS+=("$AI_PID"); fi

wait "${PIDS[@]}"
