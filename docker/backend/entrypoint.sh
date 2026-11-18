#!/bin/sh
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# MAS Lab backend entrypoint — workspace + data roots, then any CLI command.
set -eu

MAS_INSTALL_ROOT="${MAS_INSTALL_ROOT:-/opt/mas-lab}"
MAS_WORKSPACE_ROOT="${MAS_WORKSPACE_ROOT:-/workspace}"
MAS_DATA_ROOT="${MAS_DATA_ROOT:-/data}"

export MAS_INSTALL_ROOT
export MAS_WORKSPACE_ROOT
export MAS_DATA_ROOT
export MAS_TRACE_CACHE="${MAS_TRACE_CACHE:-${MAS_DATA_ROOT}/trace-cache}"
export MAS_LABS_ROOT="${MAS_LABS_ROOT:-${MAS_DATA_ROOT}/labs}"
export MAS_RUNS_ROOT="${MAS_RUNS_ROOT:-${MAS_DATA_ROOT}/runs}"
export MAS_LAB_ROOT="${MAS_LAB_ROOT:-${MAS_WORKSPACE_ROOT}}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

mkdir -p "${MAS_DATA_ROOT}" "${MAS_TRACE_CACHE}" "${MAS_LABS_ROOT}" "${MAS_RUNS_ROOT}"

# Use mounted workspace for config discovery (MAS_WORKSPACE_ROOT) when it has
# project markers; otherwise fall back to the baked install tree.  Always run
# uv from MAS_INSTALL_ROOT so we do not re-sync against the mounted pyproject.toml.
# --no-sync: the image was built with `uv sync --no-dev`; without the flag `uv run` installs the dev
# dependency group (pytest, pyarrow, ...) again in every new container, needing network and time.
if [ ! -f "${MAS_WORKSPACE_ROOT}/config.yaml" ] \
   && [ ! -d "${MAS_WORKSPACE_ROOT}/labs" ] \
   && [ ! -f "${MAS_WORKSPACE_ROOT}/mas.yaml" ]; then
  export MAS_WORKSPACE_ROOT="${MAS_INSTALL_ROOT}"
  export MAS_LAB_ROOT="${MAS_INSTALL_ROOT}"
fi

cd "${MAS_INSTALL_ROOT}"

PORT="${MAS_CONTROLLER_PORT:-8090}"

if [ "$#" -eq 0 ]; then
  set -- uvicorn mas.lab.controller.fastapi_app:app --host 0.0.0.0 --port "${PORT}"
elif [ "$1" = "controller" ] || [ "$1" = "serve" ]; then
  shift
  set -- uvicorn mas.lab.controller.fastapi_app:app --host 0.0.0.0 --port "${PORT}" "$@"
fi

exec uv run --no-sync "$@"
