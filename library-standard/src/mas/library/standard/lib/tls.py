#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TLS verification for HTTP clients."""

from __future__ import annotations

import logging
import ssl
import sys
from pathlib import Path
from typing import Any

from mas.runtime import hostenv

logger = logging.getLogger(__name__)


def _system_ssl_context() -> ssl.SSLContext | None:
    """Use OS trust store (macOS Keychain, Windows cert store) when available."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return None


def _default_ca_bundle() -> str | None:
    """Prefer certifi PEM path; fall back to common platform bundle locations."""
    for env_key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "MAS_LLM_CA_BUNDLE"):
        raw = hostenv.get(env_key, "").strip()
        if raw and Path(raw).is_file():
            return raw
    try:
        import certifi

        path = certifi.where()
        if Path(path).is_file():
            return path
    except ImportError:
        pass
    if sys.platform == "darwin":
        for candidate in (
            "/etc/ssl/cert.pem",
            "/private/etc/ssl/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
        ):
            if Path(candidate).is_file():
                return candidate
    return None


def resolve_ssl_verify(llm_proxy: dict[str, Any] | None = None) -> bool | str | ssl.SSLContext:
    """Return httpx ``verify`` argument: SSLContext, CA bundle path, or False."""
    env = hostenv.get("MAS_LLM_VERIFY_SSL", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        logger.warning("MAS_LLM_VERIFY_SSL disables TLS certificate verification (dev only)")
        return False

    proxy = llm_proxy or {}
    if proxy.get("verify_ssl") is False:
        logger.warning("infra llm_proxy.verify_ssl=false — TLS verification disabled")
        return False

    for key in ("ca_bundle",):
        raw = proxy.get(key)
        if raw:
            path = str(raw)
            ctx = _system_ssl_context()
            if ctx is not None:
                ctx.load_verify_locations(cafile=path)
                return ctx
            return path

    ctx = _system_ssl_context()
    if ctx is not None:
        return ctx

    bundle = _default_ca_bundle()
    if bundle:
        return bundle

    logger.warning("No CA bundle found (install truststore + certifi). Falling back to httpx default verification.")
    return True
