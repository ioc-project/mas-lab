#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""W3C ``traceparent`` minting. The stack stamps it on every send."""

from __future__ import annotations

import re
import secrets

_TRACEPARENT = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
    re.IGNORECASE,
)


def child_traceparent(parent: str | None) -> str:
    """Same trace id as a valid ``parent`` with a new span id, or a new root.

    The span id is a correlation id for this send, not an OpenTelemetry span.
    Version ``ff`` and all-zero trace or span ids are not valid parents.
    """
    if parent:
        match = _TRACEPARENT.match(parent.strip())
        if match:
            version, trace_id, span_id, flags = (part.lower() for part in match.groups())
            if version != "ff" and trace_id != "0" * 32 and span_id != "0" * 16:
                return f"{version}-{trace_id}-{secrets.token_hex(8)}-{flags}"
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
