#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SLIM url and secret checks. The plugin reveals a SecretRef at the call that needs it."""

from __future__ import annotations

from mas.library.standard.lib.comm.endpoint import split_url
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import is_loopback_host

_MIN_SECRET = 32


def require_secret(secret: str) -> str:
    """The shared secret, or ``AgentCommError``. The message says what is wrong, never what the secret is."""
    value = str(secret or "")
    if not value:
        raise AgentCommError("SLIM link requires a secret value")
    if len(value.encode("utf-8")) < _MIN_SECRET:
        raise AgentCommError(f"SLIM shared secret is too short: at least {_MIN_SECRET} bytes are needed")
    return value


def _split(url: str) -> tuple[str, str]:
    """``(scheme, host)`` of a SLIM endpoint, or AgentCommError when the url is not one this plugin accepts."""
    return split_url(url, what="SLIM endpoint", default_scheme="http")


def use_insecure_client_config(url: str, *, dev: bool = False) -> bool:
    """HTTP is always insecure. ``dev`` skip-verifies TLS on loopback HTTPS."""
    scheme, host = _split(url)
    if scheme == "http":
        return True
    return bool(dev) and is_loopback_host(host)


def assert_endpoint_allowed(url: str, *, dev: bool = False) -> None:
    """Raise unless ``url`` is https, or plain http on a loopback host."""
    use_insecure_client_config(url, dev=dev)
