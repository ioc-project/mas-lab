#!/usr/bin/env bash
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# Start mas-lab serve and the demo-ui Vite app together. Invoked by: task demo-ui
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv/bin}"
HOST="${DEMO_UI_HOST:-127.0.0.1}"
API_PORT="${DEMO_UI_API_PORT:-8090}"
UI_PORT="${DEMO_UI_UI_PORT:-5174}"

_prepend_env() {
  local name="$1"
  local val="$2"
  local cur=""
  [ -n "$val" ] || return 0
  eval "cur=\"\${$name-}\""
  case ":$cur:" in
    *":$val:"*) return 0 ;;
  esac
  if [ -n "$cur" ]; then
    export "$name=$val:$cur"
  else
    export "$name=$val"
  fi
}

_abs_dir() {
  local raw="$1"
  if [ -d "$raw" ]; then
    (cd "$raw" && pwd)
  elif [ -f "$raw" ]; then
    (cd "$(dirname "$raw")" && pwd)
  else
    return 1
  fi
}

# A library / lab / repo, a demo-ui/layouts directory, or a layout YAML file.
_absorb_path() {
  local raw="$1"
  [ -n "$raw" ] || return 0
  local p child
  if ! p="$(_abs_dir "$raw")"; then
    echo "demo-ui: path not found: $raw" >&2
    exit 1
  fi
  if [ -f "$raw" ]; then
    _prepend_env MAS_DEMO_UI_LAYOUTS "$p"
    return 0
  fi
  _prepend_env MAS_LIBRARY_PATHS "$p"
  if [ -d "$p/src" ]; then
    _prepend_env PYTHONPATH "$p/src"
  fi
  if [ -d "$p/demo-ui/layouts" ]; then
    _prepend_env MAS_DEMO_UI_LAYOUTS "$p/demo-ui/layouts"
  fi
  if [ "$(basename "$p")" = "layouts" ]; then
    _prepend_env MAS_DEMO_UI_LAYOUTS "$p"
  fi
  for child in "$p"/*; do
    [ -d "$child" ] || continue
    if [ -d "$child/demo-ui/layouts" ]; then
      _prepend_env MAS_LIBRARY_PATHS "$child"
      _prepend_env MAS_DEMO_UI_LAYOUTS "$child/demo-ui/layouts"
    fi
    if [ -d "$child/src" ]; then
      _prepend_env PYTHONPATH "$child/src"
    fi
  done
}

_absorb_path "${DEMO_UI_LIBRARY:-}"
_absorb_path "${LIBRARY:-}"
_absorb_path "${LAYOUTS:-}"
for raw in "$@"; do
  _absorb_path "$raw"
done

for port in "$API_PORT" "$UI_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop that process, then retry." >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
    exit 1
  fi
done

if [ ! -x "$VENV/mas-lab" ]; then
  echo "Missing $VENV/mas-lab — run: task install" >&2
  exit 1
fi
if [ ! -d "$ROOT/demo-ui/node_modules" ]; then
  npm --prefix "$ROOT/demo-ui" install
fi

export MAS_CONTROLLER_URL="http://${HOST}:${API_PORT}"
export MAS_DEMO_UI_DEV_ORIGIN="http://${HOST}:${UI_PORT}"
export MAS_DEMO_UI_ROOT="${MAS_DEMO_UI_ROOT:-$ROOT/demo-ui}"
export MAS_DEMO_UI_INGEST_URL="http://${HOST}:${API_PORT}/api/ingest"
export MAS_CONTROLLER_IDLE_SEC="${MAS_CONTROLLER_IDLE_SEC:-86400}"
export MAS_CONTROLLER_PORT="$API_PORT"
export MAS_HOME="${MAS_HOME:-$ROOT/.demo-ui}"
mkdir -p "$MAS_HOME"

echo "API      http://${HOST}:${API_PORT}"
echo "UI       http://${HOST}:${API_PORT}/"
echo "         http://${HOST}:${UI_PORT}/"
echo "Ingest   ${MAS_DEMO_UI_INGEST_URL}"
if [ -n "${MAS_LIBRARY_PATHS:-}" ]; then
  echo "Library  ${MAS_LIBRARY_PATHS}"
fi
if [ -n "${MAS_DEMO_UI_LAYOUTS:-}" ]; then
  echo "Layouts  ${MAS_DEMO_UI_LAYOUTS}"
fi

"$VENV/mas-lab" serve --port "$API_PORT" &
SERVE_PID=$!
VITE_PID=""
cleanup() {
  trap - EXIT
  kill "$SERVE_PID" ${VITE_PID:+"$VITE_PID"} 2>/dev/null || true
  wait "$SERVE_PID" ${VITE_PID:+"$VITE_PID"} 2>/dev/null || true
}
trap cleanup EXIT

ready=0
for _ in $(seq 1 80); do
  if curl -sf "http://${HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
if [ "$ready" != 1 ]; then
  echo "mas-lab serve did not become healthy on :${API_PORT}" >&2
  exit 1
fi

npm --prefix "$ROOT/demo-ui" run dev &
VITE_PID=$!
wait "$SERVE_PID" "$VITE_PID"
