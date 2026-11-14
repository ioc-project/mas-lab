#!/usr/bin/env bash
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PY_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PY_BIN" ]]; then
  uv venv "$VENV_DIR" --python 3.13
fi

if [[ -n "${SCOPE_ONTOLOGY_EDITABLE:-}" ]] && [[ -d "${SCOPE_ONTOLOGY_EDITABLE}" ]]; then
  uv pip install --python "$PY_BIN" -e "$SCOPE_ONTOLOGY_EDITABLE"
fi

# [all] (defined in pyproject.toml) installs every optional extra,
# including [neo4j] (the driver) and [bench], so no import path goes
# untested -- that's what a complete CI run uses. NO_OPTIONAL=true installs
# just [dev] instead, for a faster local loop: neo4j.py/dump.py's own tests
# don't actually need the package installed either way (GraphDatabase is a
# per-call lazy import; nothing here exercises a live server), so this only
# affects whether the driver's own import path is exercised, never test
# correctness.
if [[ "${NO_OPTIONAL:-false}" == "true" ]]; then
  uv pip install --python "$PY_BIN" -e "$ROOT_DIR"
  uv pip install --python "$PY_BIN" \
    pytest pytest-cov pytest-asyncio click ruff rdflib pyshacl jsonschema pyyaml
else
  uv pip install --python "$PY_BIN" -e "$ROOT_DIR[all]"
fi
