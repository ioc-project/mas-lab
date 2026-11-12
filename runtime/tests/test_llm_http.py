#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for LLM HTTP retry classification. Retry itself is a layer around the provider."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from mas.library.standard.plugins.llm.http import (
    arequest_once,
    llm_status_is_retryable,
    request_once,
)
from mas.runtime.reliability.classify import classify_llm_failure, is_retryable_llm_http_error, is_tls_verify_failure
from mas.runtime.reliability.policy import RetryPolicy
from mas.runtime.reliability.retry import acall_with_retry, call_with_retry


def _fast_retry(*, max_attempts: int = 4):
    return RetryPolicy.from_mapping(
        {"max_attempts": max_attempts, "backoff_s": 0.001, "jitter": False},
        field_name="retry",
        default=RetryPolicy.llm_default(),
    )


def _retrying_request(client, method, url, **kwargs):
    policy = kwargs.pop("retry_policy", _fast_retry())
    return call_with_retry(
        lambda: request_once(client, method, url, **kwargs),
        policy=policy,
        classify=classify_llm_failure,
        idempotent=True,
    )


async def _aretrying_request(client, method, url, **kwargs):
    policy = kwargs.pop("retry_policy", _fast_retry())
    return await acall_with_retry(
        lambda: arequest_once(client, method, url, **kwargs),
        policy=policy,
        classify=classify_llm_failure,
        idempotent=True,
    )


def test_is_retryable_for_connect_and_timeout_errors():
    assert is_retryable_llm_http_error(httpx.ConnectError("boom")) is True
    assert is_retryable_llm_http_error(httpx.TimeoutException("boom")) is True
    assert is_retryable_llm_http_error(httpx.ConnectError("Connection refused")) is True


def test_is_retryable_for_5xx_and_429_status():
    for status in (429, 500, 502, 503, 504):
        assert llm_status_is_retryable(status) is True
        response = httpx.Response(status, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("boom", request=response.request, response=response)
        assert is_retryable_llm_http_error(exc) is True


def test_not_retryable_for_4xx_status():
    response = httpx.Response(400, request=httpx.Request("POST", "https://x"))
    exc = httpx.HTTPStatusError("boom", request=response.request, response=response)
    assert is_retryable_llm_http_error(exc) is False
    assert llm_status_is_retryable(400) is False


def test_not_retryable_for_tls_verify_failure():
    exc = ssl_cert_error()
    assert is_tls_verify_failure(exc) is True
    assert is_retryable_llm_http_error(exc) is False


def test_retryable_for_transient_socket_markers():
    assert is_retryable_llm_http_error(RuntimeError("Connection reset by peer")) is True
    assert is_retryable_llm_http_error(RuntimeError("totally unrelated")) is False


def ssl_cert_error() -> Exception:
    import ssl

    return ssl.SSLCertVerificationError("certificate verify failed")


def test_retrying_request_succeeds_after_transient_failures():
    ok_response = MagicMock(status_code=200)
    client = MagicMock()
    client.request.side_effect = [httpx.ConnectError("boom"), httpx.ConnectError("boom"), ok_response]

    result = _retrying_request(client, "POST", "https://x", retry_policy=_fast_retry())

    assert result is ok_response
    assert client.request.call_count == 3


def test_retrying_request_gives_up_on_non_retryable_error():
    client = MagicMock()
    client.request.side_effect = ValueError("not retryable")

    with pytest.raises(Exception) as caught:
        _retrying_request(client, "POST", "https://x", retry_policy=_fast_retry())

    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    assert isinstance(caught.value, ClassifiedFailure)
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert client.request.call_count == 1


def test_retrying_request_exhausts_budget_and_raises_last_error():
    client = MagicMock()
    client.request.side_effect = httpx.TimeoutException("boom")

    with pytest.raises(Exception) as caught:
        _retrying_request(client, "POST", "https://x", retry_policy=_fast_retry(max_attempts=3))

    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    assert isinstance(caught.value, ClassifiedFailure)
    assert caught.value.failure_class is FailureClass.TRANSIENT
    assert client.request.call_count == 3


def test_retrying_request_retries_http_500():
    req = httpx.Request("POST", "https://x")
    fail = httpx.Response(500, request=req)
    ok = httpx.Response(200, request=req)
    client = MagicMock()
    client.request.side_effect = [fail, ok]

    result = _retrying_request(client, "POST", "https://x", retry_policy=_fast_retry())

    assert result is ok
    assert client.request.call_count == 2


def test_retrying_request_retries_connection_refused():
    ok = MagicMock(status_code=200)
    client = MagicMock()
    client.request.side_effect = [httpx.ConnectError("Connection refused"), ok]

    result = _retrying_request(client, "POST", "https://x", retry_policy=_fast_retry())

    assert result is ok
    assert client.request.call_count == 2


def test_retrying_request_get_client_and_on_failure():
    created: list[object] = []
    failures: list[BaseException] = []

    class _FactoryClient:
        def request(self, method, url, **kwargs):
            if len(created) == 1:
                raise httpx.ConnectError("boom")
            return MagicMock(status_code=200)

    def factory():
        client = _FactoryClient()
        created.append(client)
        return client

    result = _retrying_request(
        None,
        "POST",
        "https://x",
        get_client=factory,
        on_failure=failures.append,
        retry_policy=_fast_retry(),
    )

    assert result.status_code == 200
    assert len(created) == 2
    assert len(failures) == 1
    assert isinstance(failures[0], httpx.ConnectError)


@pytest.mark.asyncio
async def test_aretrying_request_retries_timeout():
    ok = MagicMock(status_code=200)
    calls = {"n": 0}
    seq: list[object] = [httpx.TimeoutException("boom"), ok]

    async def request(method, url, **kwargs):
        calls["n"] += 1
        item = seq[calls["n"] - 1]
        if isinstance(item, BaseException):
            raise item
        return item

    client = MagicMock()
    client.request = request

    result = await _aretrying_request(client, "POST", "https://x", retry_policy=_fast_retry())

    assert result is ok
    assert calls["n"] == 2
