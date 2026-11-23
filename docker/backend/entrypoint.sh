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
MOUNTED_WORKSPACE="${MAS_WORKSPACE_ROOT}"
if [ ! -f "${MAS_WORKSPACE_ROOT}/config.yaml" ] \
   && [ ! -d "${MAS_WORKSPACE_ROOT}/labs" ] \
   && [ ! -f "${MAS_WORKSPACE_ROOT}/mas.yaml" ]; then
  export MAS_WORKSPACE_ROOT="${MAS_INSTALL_ROOT}"
  export MAS_LAB_ROOT="${MAS_INSTALL_ROOT}"
fi

# Who the server runs as. The image has no USER: it starts as root. On a Linux bind mount, files
# root writes are root's on the host, and the secret store refuses files another user owns. So
# when the mounted workspace belongs to a user, the server runs as that user (uid and gid of the
# mount), and what it writes (secrets included) is that user's on the host and in the container.
RUN_UID="$(id -u)"
RUN_GID="$(id -g)"
if [ "${RUN_UID}" = "0" ] && [ -d "${MOUNTED_WORKSPACE}" ]; then
  RUN_UID="$(stat -c %u "${MOUNTED_WORKSPACE}")"
  RUN_GID="$(stat -c %g "${MOUNTED_WORKSPACE}")"
fi

# The secret file store stays on the mounted workspace, never in the container layer: when the
# install tree is the workspace, its default store <install>/.mas/secrets points at the mount's.
# The store refuses a directory other users can read, or one another user owns, so a new one is
# created 0700 and owned by the user the server runs as.
if [ -d "${MOUNTED_WORKSPACE}" ]; then
  if [ ! -e "${MOUNTED_WORKSPACE}/.mas/secrets" ]; then
    mkdir -p "${MOUNTED_WORKSPACE}/.mas"
    mkdir -m 700 "${MOUNTED_WORKSPACE}/.mas/secrets"
    if [ "$(id -u)" = "0" ]; then
      chown "${RUN_UID}:${RUN_GID}" "${MOUNTED_WORKSPACE}/.mas" "${MOUNTED_WORKSPACE}/.mas/secrets"
    fi
  elif [ "$(id -u)" = "0" ] && [ "${RUN_UID}" != "0" ] && [ -d "${MOUNTED_WORKSPACE}/.mas/secrets" ] \
       && [ "$(stat -c %u "${MOUNTED_WORKSPACE}/.mas/secrets")" = "0" ]; then
    # A store an earlier image wrote as root: hand it to the workspace's owner, modes unchanged.
    chown -R "${RUN_UID}:${RUN_GID}" "${MOUNTED_WORKSPACE}/.mas/secrets"
  fi
  if [ "${MAS_WORKSPACE_ROOT}" != "${MOUNTED_WORKSPACE}" ]; then
    mkdir -p "${MAS_WORKSPACE_ROOT}/.mas"
    ln -sfn "${MOUNTED_WORKSPACE}/.mas/secrets" "${MAS_WORKSPACE_ROOT}/.mas/secrets"
    if [ "$(id -u)" = "0" ]; then
      chown -h "${RUN_UID}:${RUN_GID}" "${MAS_WORKSPACE_ROOT}/.mas" "${MAS_WORKSPACE_ROOT}/.mas/secrets"
    fi
  fi
fi

cd "${MAS_INSTALL_ROOT}"

PORT="${MAS_CONTROLLER_PORT:-8090}"

if [ "$#" -eq 0 ]; then
  set -- uvicorn --factory mas.lab.controller.fastapi_app:served_app --host 0.0.0.0 --port "${PORT}"
elif [ "$1" = "controller" ] || [ "$1" = "serve" ]; then
  shift
  set -- uvicorn --factory mas.lab.controller.fastapi_app:served_app --host 0.0.0.0 --port "${PORT}" "$@"
fi

if [ "$(id -u)" = "0" ] && [ "${RUN_UID}" != "0" ]; then
  # The data roots this script created, and a home for the user (uv's cache).
  export HOME="${MAS_DATA_ROOT}/home"
  export USER=mas LOGNAME=mas  # no passwd entry for a host uid
  mkdir -p "${HOME}"
  chown "${RUN_UID}:${RUN_GID}" "${MAS_DATA_ROOT}" "${MAS_TRACE_CACHE}" "${MAS_LABS_ROOT}" "${MAS_RUNS_ROOT}" "${HOME}"
  exec setpriv --reuid="${RUN_UID}" --regid="${RUN_GID}" --clear-groups uv run --no-sync "$@"
fi
exec uv run --no-sync "$@"
