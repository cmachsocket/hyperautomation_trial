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
START_MCP_VALUE="${START_MCP:-1}"

resolve_python_runner() {
  if command -v uv >/dev/null 2>&1; then
    echo "uv"
    return 0
  fi

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
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

if ! PYTHON_RUNNER_VALUE="$(resolve_python_runner)"; then
  echo "[start_servers] error: uv or python/python3 not found."
  exit 1
fi

if is_port_listening "$WS_PORT_VALUE"; then
  echo "[start_servers] ws port $WS_PORT_VALUE already in use, skip starting ws_server.py"
else
  # 把 ws_server 的输出重定向到文件，避免掩盖 MCP 的输出
  WS_LOG_FILE="${ROOT_DIR}/ws_server.log"
  if [[ "$PYTHON_RUNNER_VALUE" == "uv" ]]; then
    uv run --no-sync -- python -m server.ws_server >> "$WS_LOG_FILE" 2>&1 &
  else
    "$PYTHON_RUNNER_VALUE" -m server.ws_server >> "$WS_LOG_FILE" 2>&1 &
  fi
  WS_PID=$!
fi

if [[ -n "${WS_PID:-}" ]]; then
  echo "ws_server.py started (pid=$WS_PID, port=$WS_PORT_VALUE)"
  echo "ws_server logs: $WS_LOG_FILE"
fi

if [[ "$START_MCP_VALUE" != "0" ]]; then
  echo ""
  echo "================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting MCP stdio server"
  echo "================================"
  echo ""
  if [[ "$PYTHON_RUNNER_VALUE" == "uv" ]]; then
    # 用 -u 标志禁用 Python 的输出缓冲
    uv run --no-sync -- python -u -m server.ai.ai_controller_fastmcp
  else
    # 用 -u 标志禁用 Python 的输出缓冲
    "$PYTHON_RUNNER_VALUE" -u -m server.ai.ai_controller_fastmcp
  fi
  exit $?
fi

if [[ -z "${WS_PID:-}" ]]; then
  echo "[start_servers] ws service is already running, nothing to start"
  exit 0
fi

echo "Press Ctrl+C to stop started services"
wait "$WS_PID"
