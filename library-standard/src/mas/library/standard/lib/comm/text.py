#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Text from outside the process, made safe to put in an error or a log line."""

from __future__ import annotations

import unicodedata

# One cap for every error and log line that quotes text a peer, a server or a library sent. A peer
# can send megabytes; an error that repeats them is a denial of service and a prompt-injection channel.
MAX_ERROR_TEXT = 256

_BREAKS = frozenset("\t\n\r\v\f")
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def safe_text(value: object, *, limit: int = MAX_ERROR_TEXT) -> str:
    """``value`` as one short printable line.

    Line and tab breaks become one space, every other control, format (bidi, zero-width) and
    line-separator character is dropped, and the result is cut to ``limit`` characters, ending in
    an ellipsis when it was cut. A value that is not text is read through ``str``.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    text = value if isinstance(value, str) else str(value)
    out: list[str] = []
    for char in text:
        if char in _BREAKS:
            out.append(" ")
        elif unicodedata.category(char) not in _DROPPED_CATEGORIES:
            out.append(char)
    cleaned = "".join(out).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
