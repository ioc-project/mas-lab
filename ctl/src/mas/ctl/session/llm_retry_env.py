#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Last-resort interactive ``MAS_LLM_HTTP_*`` overrides. Runtime does not read env."""

from __future__ import annotations

import os
from typing import Any


def llm_http_retry_overrides() -> dict[str, Any]:
    """Param overlay for the llm retry layer. Empty when the env vars are unset."""
    out: dict[str, Any] = {}
    if "MAS_LLM_HTTP_RETRIES" in os.environ:
        raw = os.environ.get("MAS_LLM_HTTP_RETRIES") or "3"
        out["max_attempts"] = max(1, int(raw) + 1)
    if "MAS_LLM_HTTP_RETRY_BACKOFF" in os.environ:
        raw = os.environ.get("MAS_LLM_HTTP_RETRY_BACKOFF") or "0.5"
        out["backoff_s"] = max(0.05, float(raw))
        out["jitter"] = False
    return out
