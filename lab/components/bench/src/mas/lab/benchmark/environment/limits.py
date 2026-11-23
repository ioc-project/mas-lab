#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback / allow-list checks for environment http_call."""

from __future__ import annotations

from urllib.parse import urlparse

from mas.runtime.secrets import is_loopback_host


def assert_url_allowed(url: str, allow_network: tuple[str, ...] = ()) -> str:
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"http_call url must be http or https, got scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if is_loopback_host(host):
        return str(url)
    allowed = {item.lower() for item in allow_network}
    if host in allowed or str(url) in allow_network:
        return str(url)
    raise ValueError(f"http_call url must be loopback or allow-listed, got host {host!r}")
