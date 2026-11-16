#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared constants for the FastAPI controller API."""

from __future__ import annotations

import os
from pathlib import Path

from mas.runtime.xdg import mas_cache_root, mas_data_root

LIBRARIES_DIR = Path(
    os.getenv(
        "MAS_LIBRARIES_DIR",
        str(Path.home() / "mas-lab"),
    )
)

DEFAULT_MAX_TIMEOUT = 1800
# Request models keep fixed defaults up to this value; a lower cap would let them exceed it.
MIN_MAX_TIMEOUT = 300


def parse_max_timeout(raw: str | None) -> int:
    """Return the job timeout cap in seconds from a MAS_CONTROLLER_MAX_TIMEOUT value."""
    if raw is None or not raw.strip():
        return DEFAULT_MAX_TIMEOUT
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"MAS_CONTROLLER_MAX_TIMEOUT must be an integer number of seconds, got {raw!r}") from exc
    if value < MIN_MAX_TIMEOUT:
        raise RuntimeError(
            f"MAS_CONTROLLER_MAX_TIMEOUT must be at least {MIN_MAX_TIMEOUT}, got {value}: "
            f"some request timeouts default to {MIN_MAX_TIMEOUT}s"
        )
    return value


MAX_TIMEOUT = parse_max_timeout(os.environ.get("MAS_CONTROLLER_MAX_TIMEOUT"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

MAS_LAB_ROOT = Path(os.environ.get("MAS_LAB_ROOT", mas_data_root()))

WEB_SEARCH_CACHE_DIR = mas_cache_root() / "web_search"

HIDDEN_FILES = {".DS_Store", ".run_ref", ".gitkeep"}

SCHEMAS_DIR = Path(__file__).parent / "schemas"
PIPELINE_STEP_TYPES_PRE_PATH = SCHEMAS_DIR / "pipeline-step-types-pre.json"
PIPELINE_STEP_TYPES_POST_PATH = SCHEMAS_DIR / "pipeline-step-types-post.json"
