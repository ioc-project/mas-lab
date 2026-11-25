#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""How an HTTP server tells a caller what would satisfy it: a 401 with ``WWW-Authenticate``."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.stack import PeerChallenge


def _header_safe(text: str) -> bool:
    return 0 < len(text) <= 4096 and all(32 <= ord(ch) < 127 for ch in text)


def www_authenticate(challenge: Any, scheme: str = "Challenge", wire: str | None = None) -> str:
    """A ``WWW-Authenticate`` value for a peer challenge: ``scheme`` and its scalar fields as parameters (a value that
    is not printable ASCII, or is empty or over 4096 characters, is left out: it is in the 401 body).

    ``wire`` (the serialized challenge a scheme built for a peer with its own shape) is carried verbatim after the
    scheme when it is plain printable ASCII; otherwise the body of the 401 is the only place it travels. The
    structured challenge is also in the body of the 401; the header carries what a generic HTTP client reads.
    """
    if wire is not None and _header_safe(wire):
        return f"{scheme} {wire}"
    params = []
    for key, value in challenge.items():
        if not str(key).isidentifier() or key == "schemes":
            continue
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value) and value:
            value = " ".join(value)  # a list of names is one space-separated parameter
        if isinstance(value, (str, int, float, bool)):
            if not _header_safe(str(value)):  # a CR, LF or non-ASCII value would split or corrupt the header: dropped
                continue
            text = str(value).replace("\\", "\\\\").replace('"', '\\"')
            params.append(f'{key}="{text}"')
    return f"{scheme} " + ", ".join(params) if params else scheme


def challenge_headers(refused: PeerChallenge, default_scheme: str) -> dict[str, str]:
    """The response headers for a refused request: ``WWW-Authenticate`` in the scheme the verifier asked for."""
    scheme = refused.carry.get("http.auth_scheme") or default_scheme
    return {"WWW-Authenticate": www_authenticate(refused.challenge, scheme, refused.wire)}
