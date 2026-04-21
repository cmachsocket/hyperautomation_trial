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

START_BEEPER_VALUE="${START_BEEPER:-1}"
START_MQ2_VALUE="${START_MQ2:-1}"

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

cleanup() {
  local exit_code=$?
  if [[ -n "${BEEPER_PID:-}" ]]; then kill "$BEEPER_PID" 2>/dev/null || true; fi
  if [[ -n "${MQ2_PID:-}" ]]; then kill "$MQ2_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

if ! PYTHON_BIN_VALUE="$(resolve_python_bin)"; then
  echo "[start_devices] error: python/python3 not found."
  exit 1
fi

if [[ "$START_BEEPER_VALUE" != "0" ]]; then
  "$PYTHON_BIN_VALUE" devices/beeper.py &
  BEEPER_PID=$!
  echo "[start_devices] beeper started (pid=$BEEPER_PID)"
else
  echo "[start_devices] START_BEEPER=0, skip beeper"
fi

if [[ "$START_MQ2_VALUE" != "0" ]]; then
  "$PYTHON_BIN_VALUE" devices/mq2.py &
  MQ2_PID=$!
  echo "[start_devices] mq2 started (pid=$MQ2_PID)"
else
  echo "[start_devices] START_MQ2=0, skip mq2"
fi

if [[ -z "${BEEPER_PID:-}" && -z "${MQ2_PID:-}" ]]; then
  echo "[start_devices] nothing to start"
  exit 0
fi

echo "Press Ctrl+C to stop started device clients"
wait
