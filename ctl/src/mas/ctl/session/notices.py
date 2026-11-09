#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One-line warnings on stderr that a run prints once however often its resolvers are called."""

from __future__ import annotations

import sys

_PRINTED: set[str] = set()


def warn_once(message: str) -> None:
    """Print ``warning: <message>`` on stderr the first time this process sees ``message``."""
    if message in _PRINTED:
        return
    _PRINTED.add(message)
    print(f"warning: {message}", file=sys.stderr)


def reset_notices() -> None:
    """Forget what was printed. Tests call this so each one sees its own warnings."""
    _PRINTED.clear()
