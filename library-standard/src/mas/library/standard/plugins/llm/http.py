#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HTTP client helpers for LLM provider plugins — TLS and one POST.

This is engine-execute I/O for ``llm_provider`` plugins. The kernel never
imports this module. Retry is a layer around the provider call.
"""

from __future__ import annotations

import inspect
import logging
import os
import ssl
import sys
from pathlib import Path
from typing import Any

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
        raw = os.environ.get(env_key, "").strip()
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
    env = os.environ.get("MAS_LLM_VERIFY_SSL", "").strip().lower()
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


def llm_status_is_retryable(status: int) -> bool:
    """429 and any 5xx (including 500 from an overloaded LLM server)."""
    return status == 429 or status >= 500


def _raise_if_retryable_status(resp: Any) -> Any:
    import httpx

    if llm_status_is_retryable(getattr(resp, "status_code", 0)):
        raise httpx.HTTPStatusError(
            f"LLM HTTP {resp.status_code}",
            request=resp.request,
            response=resp,
        )
    return resp


def request_once(client: Any, method: str, url: str, **kwargs: Any) -> Any:
    """One POST/GET. 429/5xx raise so a retry layer can re-issue the call."""
    get_client = kwargs.pop("get_client", None)
    on_failure = kwargs.pop("on_failure", None)
    kwargs.pop("retry_policy", None)
    kwargs.pop("circuit_breaker", None)
    kwargs.pop("retry_target", None)

    def _once() -> Any:
        c = get_client() if get_client is not None else client
        try:
            return _raise_if_retryable_status(c.request(method, url, **kwargs))
        except Exception as exc:
            if on_failure is not None:
                on_failure(exc)
            raise

    return _once()


async def arequest_once(client: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Async twin of :func:`request_once`."""
    get_client = kwargs.pop("get_client", None)
    on_failure = kwargs.pop("on_failure", None)
    kwargs.pop("retry_policy", None)
    kwargs.pop("circuit_breaker", None)
    kwargs.pop("retry_target", None)

    async def _once() -> Any:
        c = get_client() if get_client is not None else client
        try:
            return _raise_if_retryable_status(await c.request(method, url, **kwargs))
        except Exception as exc:
            if on_failure is not None:
                result = on_failure(exc)
                if inspect.isawaitable(result):
                    await result
            raise

    return await _once()
