#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HTTP client helpers for LLM provider plugins — one POST (TLS is :mod:`mas.library.standard.lib.tls`).

This is engine-execute I/O for ``llm_provider`` plugins. The kernel never
imports this module. Retry is a layer around the provider call.
"""

from __future__ import annotations

import inspect
from typing import Any

from mas.runtime.reliability.classify import is_retryable_status


def _raise_if_retryable_status(resp: Any) -> Any:
    import httpx

    if is_retryable_status(getattr(resp, "status_code", 0)):
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
