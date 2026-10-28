#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for LLM HTTP retry classification and the retrying request helper.

Live LLM calls go through an LLMProvider plugin which uses these helpers
(``request_with_retries`` / ``call_with_retry``) on the OpenAI-compatible path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from mas.library.standard.plugins.llm.http import (
    arequest_with_retries,
    llm_status_is_retryable,
    request_with_retries,
)
from mas.runtime.reliability.classify import is_retryable_llm_http_error, is_tls_verify_failure


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


def test_request_with_retries_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
    ok_response = MagicMock(status_code=200)
    client = MagicMock()
    client.request.side_effect = [httpx.ConnectError("boom"), httpx.ConnectError("boom"), ok_response]

    result = request_with_retries(client, "POST", "https://x")

    assert result is ok_response
    assert client.request.call_count == 3


def test_request_with_retries_gives_up_on_non_retryable_error(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
    client = MagicMock()
    client.request.side_effect = ValueError("not retryable")

    with pytest.raises(Exception) as caught:
        request_with_retries(client, "POST", "https://x")

    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    assert isinstance(caught.value, ClassifiedFailure)
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert client.request.call_count == 1


def test_request_with_retries_exhausts_budget_and_raises_last_error(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "2")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
    client = MagicMock()
    client.request.side_effect = httpx.TimeoutException("boom")

    with pytest.raises(Exception) as caught:
        request_with_retries(client, "POST", "https://x")

    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    assert isinstance(caught.value, ClassifiedFailure)
    assert caught.value.failure_class is FailureClass.TRANSIENT
    assert client.request.call_count == 3


def test_request_with_retries_retries_http_500(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
    req = httpx.Request("POST", "https://x")
    fail = httpx.Response(500, request=req)
    ok = httpx.Response(200, request=req)
    client = MagicMock()
    client.request.side_effect = [fail, ok]

    result = request_with_retries(client, "POST", "https://x")

    assert result is ok
    assert client.request.call_count == 2


def test_request_with_retries_retries_connection_refused(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
    ok = MagicMock(status_code=200)
    client = MagicMock()
    client.request.side_effect = [httpx.ConnectError("Connection refused"), ok]

    result = request_with_retries(client, "POST", "https://x")

    assert result is ok
    assert client.request.call_count == 2


def test_request_with_retries_get_client_and_on_failure(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
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

    result = request_with_retries(
        None,
        "POST",
        "https://x",
        get_client=factory,
        on_failure=failures.append,
    )

    assert result.status_code == 200
    assert len(created) == 2
    assert len(failures) == 1
    assert isinstance(failures[0], httpx.ConnectError)


@pytest.mark.asyncio
async def test_arequest_with_retries_retries_timeout(monkeypatch):
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "3")
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.001")
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

    result = await arequest_with_retries(client, "POST", "https://x")

    assert result is ok
    assert calls["n"] == 2
