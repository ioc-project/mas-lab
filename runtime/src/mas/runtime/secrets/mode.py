#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The owner and mode rule of a secret file or directory, shared by the file store and its writers."""

from __future__ import annotations

import os
import sys


def check_private_mode(st: os.stat_result, *, kind: str) -> str | None:
    """Why *st* is not private to this user, or ``None`` (always on Windows, which has no such bits)."""
    if sys.platform == "win32":
        return None
    if st.st_uid != os.geteuid():
        return f"{kind} is not owned by this user"
    if st.st_mode & 0o077:
        return f"{kind} is group or world accessible"
    return None
