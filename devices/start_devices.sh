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

usage() {
  cat <<'EOF'
Usage: devices/start_devices.sh [options]

Options:
  --devices <list>   Comma-separated device names: beeper,mq2,bme280,oled
                     Special values: all, none
  --beeper           Enable beeper
  --no-beeper        Disable beeper
  --mq2              Enable mq2
  --no-mq2           Disable mq2
  --bme280           Enable bme280
  --no-bme280        Disable bme280
  --oled             Enable oled
  --no-oled          Disable oled
  --interactive      Ask per-device startup choices in terminal
  --list             Show current selection and exit
  -h, --help         Show this help

Defaults come from env vars START_BEEPER/START_MQ2/START_BME280/START_OLED.
EOF
}

is_enabled() {
  local v="${1:-1}"
  case "${v,,}" in
    0|false|no|off) return 1 ;;
    *) return 0 ;;
  esac
}

validate_device_name() {
  local name="$1"
  case "$name" in
    beeper|mq2|bme280|oled) return 0 ;;
    *) return 1 ;;
  esac
}

declare -A ENABLED
declare -A PIDS
ENABLED[beeper]=0
ENABLED[mq2]=0
ENABLED[bme280]=0
ENABLED[oled]=0

if is_enabled "${START_BEEPER:-1}"; then ENABLED[beeper]=1; fi
if is_enabled "${START_MQ2:-1}"; then ENABLED[mq2]=1; fi
if is_enabled "${START_BME280:-1}"; then ENABLED[bme280]=1; fi
if is_enabled "${START_OLED:-1}"; then ENABLED[oled]=1; fi

set_device_enabled() {
  local name="$1"
  local val="$2"
  ENABLED["$name"]="$val"
}

set_from_devices_arg() {
  local spec="$1"
  spec="${spec// /}"

  case "${spec,,}" in
    all)
      set_device_enabled beeper 1
      set_device_enabled mq2 1
      set_device_enabled bme280 1
      set_device_enabled oled 1
      return 0
      ;;
    none)
      set_device_enabled beeper 0
      set_device_enabled mq2 0
      set_device_enabled bme280 0
      set_device_enabled oled 0
      return 0
      ;;
  esac

  set_device_enabled beeper 0
  set_device_enabled mq2 0
  set_device_enabled bme280 0
  set_device_enabled oled 0

  local item
  IFS=',' read -r -a items <<<"$spec"
  for item in "${items[@]}"; do
    item="${item,,}"
    if ! validate_device_name "$item"; then
      echo "[start_devices] error: unknown device in --devices: $item"
      exit 1
    fi
    set_device_enabled "$item" 1
  done
}

ask_device_choice() {
  local name="$1"
  local current="$2"
  local prompt=""
  local answer=""

  if [[ "$current" == "1" ]]; then
    prompt="[Y/n]"
  else
    prompt="[y/N]"
  fi

  read -r -p "[start_devices] start ${name}? ${prompt} " answer
  answer="${answer,,}"

  if [[ -z "$answer" ]]; then
    echo "$current"
    return 0
  fi

  case "$answer" in
    y|yes) echo "1" ;;
    n|no) echo "0" ;;
    *)
      echo "[start_devices] invalid input: $answer (use y/yes or n/no)" >&2
      return 1
      ;;
  esac
}

print_selection() {
  echo "[start_devices] selection: beeper=${ENABLED[beeper]} mq2=${ENABLED[mq2]} bme280=${ENABLED[bme280]} oled=${ENABLED[oled]}"
}

INTERACTIVE=0
LIST_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --devices)
      if [[ $# -lt 2 ]]; then
        echo "[start_devices] error: --devices requires an argument"
        exit 1
      fi
      set_from_devices_arg "$2"
      shift 2
      ;;
    --beeper) set_device_enabled beeper 1; shift ;;
    --no-beeper) set_device_enabled beeper 0; shift ;;
    --mq2) set_device_enabled mq2 1; shift ;;
    --no-mq2) set_device_enabled mq2 0; shift ;;
    --bme280) set_device_enabled bme280 1; shift ;;
    --no-bme280) set_device_enabled bme280 0; shift ;;
    --oled) set_device_enabled oled 1; shift ;;
    --no-oled) set_device_enabled oled 0; shift ;;
    --interactive) INTERACTIVE=1; shift ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[start_devices] error: unknown option $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$INTERACTIVE" == "1" ]]; then
  echo "[start_devices] interactive mode"
  for dev in beeper mq2 bme280 oled; do
    while true; do
      if choice="$(ask_device_choice "$dev" "${ENABLED[$dev]}")"; then
        ENABLED["$dev"]="$choice"
        break
      fi
    done
  done
fi

if [[ "$LIST_ONLY" == "1" ]]; then
  print_selection
  exit 0
fi

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
  local dev
  for dev in "${!PIDS[@]}"; do
    kill "${PIDS[$dev]}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

if ! PYTHON_BIN_VALUE="$(resolve_python_bin)"; then
  echo "[start_devices] error: python/python3 not found."
  exit 1
fi

start_device() {
  local name="$1"
  local script="$2"
  "$PYTHON_BIN_VALUE" "$script" &
  PIDS["$name"]=$!
  echo "[start_devices] ${name} started (pid=${PIDS[$name]})"
}

print_selection

if [[ "${ENABLED[beeper]}" == "1" ]]; then
  start_device beeper devices/beeper.py
else
  echo "[start_devices] skip beeper"
fi

if [[ "${ENABLED[mq2]}" == "1" ]]; then
  start_device mq2 devices/mq2.py
else
  echo "[start_devices] skip mq2"
fi

if [[ "${ENABLED[bme280]}" == "1" ]]; then
  start_device bme280 devices/bme280.py
else
  echo "[start_devices] skip bme280"
fi

if [[ "${ENABLED[oled]}" == "1" ]]; then
  start_device oled devices/oled.py
else
  echo "[start_devices] skip oled"
fi

if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo "[start_devices] nothing to start"
  exit 0
fi

echo "Press Ctrl+C to stop started device clients"
wait
