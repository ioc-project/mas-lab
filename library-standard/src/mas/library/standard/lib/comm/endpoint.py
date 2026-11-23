#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""URL checks shared by transports that reach an endpoint over http or https."""

from __future__ import annotations

from urllib.parse import urlsplit

from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import is_loopback_host
from mas.runtime.secrets.url import has_unsafe_chars


def split_url(url: str, *, what: str, default_scheme: str = "") -> tuple[str, str]:
    """``(scheme, host)`` of an http(s) endpoint url, or ``AgentCommError`` naming ``what``.

    No credentials in the url, no backslash, space or control character,
    a host, and a scheme of ``http`` or ``https``. Plain ``http`` is only
    allowed on a loopback host.
    """
    text = str(url or "").strip()
    if not text:
        raise AgentCommError(f"{what} requires url")
    if has_unsafe_chars(text):
        raise AgentCommError(f"{what} url has a backslash, a space or a control character")
    parsed = urlsplit(text)
    if "@" in parsed.netloc:
        raise AgentCommError(f"{what} url must not carry credentials")
    scheme = (parsed.scheme or default_scheme).lower()
    if scheme not in {"http", "https"}:
        raise AgentCommError(f"{what} url scheme {scheme!r} is not supported; use http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host:
        raise AgentCommError(f"{what} url is missing a host")
    if scheme == "http" and not is_loopback_host(host):
        raise AgentCommError(
            f"plain http for {what} is only allowed on a loopback host (127.0.0.0/8, localhost, ::1); use https"
        )
    return scheme, host
