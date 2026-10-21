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

MAX_TIMEOUT = 1800
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

MAS_LAB_ROOT = Path(os.environ.get("MAS_LAB_ROOT", mas_data_root()))

WEB_SEARCH_CACHE_DIR = mas_cache_root() / "web_search"

HIDDEN_FILES = {".DS_Store", ".run_ref", ".gitkeep"}

SCHEMAS_DIR = Path(__file__).parent / "schemas"
PIPELINE_STEP_TYPES_PRE_PATH = SCHEMAS_DIR / "pipeline-step-types-pre.json"
PIPELINE_STEP_TYPES_POST_PATH = SCHEMAS_DIR / "pipeline-step-types-post.json"
