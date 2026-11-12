#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One-time deprecation warnings, shared by every spelling the runtime still accepts."""

from __future__ import annotations

import logging

logger = logging.getLogger("mas.runtime")

_warned: set[str] = set()


def warn_deprecated_once(key: str, message: str) -> None:
    """Log a deprecation warning once per process for *key*."""
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


def reset_deprecation_warnings() -> None:
    """Test helper: allow every deprecation warning again."""
    _warned.clear()
