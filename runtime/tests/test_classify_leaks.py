#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A classified failure says the status and no category, never the body, a URL query or userinfo."""

from __future__ import annotations

import httpx
import pytest
from mas.runtime.reliability.classify import classify_http_exception, classify_tool_exception, error_summary

CANARY = "sk-CANARY-KEY-123"
QUERY = "Q-CANARY"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "bad"),
        (401, "nope"),
        (403, "exceeded budget"),
        (413, "maximum context length"),
        (429, "slow"),
        (503, "down"),
    ],
)
def test_classify_status_never_copies_the_body(status: int, message: str) -> None:
    # Every status branch: the provider body (which may echo the request's key) is matched, never copied.
    request = httpx.Request("POST", f"https://llm.example/v1?token={QUERY}")
    response = httpx.Response(
        status, json={"error": {"message": f"{message}: echoed {CANARY}", "type": "t", "code": "c"}}, request=request
    )
    error = httpx.HTTPStatusError(f"error for url '{request.url}'", request=request, response=response)
    for failure in (classify_http_exception(error), classify_tool_exception(error)):
        assert f"HTTP {status}" in str(failure)
        assert CANARY not in str(failure) and QUERY not in str(failure)
        assert CANARY not in str(failure.code)
    assert error_summary(error) == f"HTTPStatusError: HTTP {status}"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError(f"Connection refused: https://u:{CANARY}@llm.example/v1?key={QUERY}"),
        httpx.ReadTimeout(f"timed out https://llm.example/v1?key={QUERY}"),
        RuntimeError(f"failed https://u:{CANARY}@llm.example/v1?key={QUERY}#{QUERY}"),
    ],
)
def test_classify_http_exception_cuts_urls_in_its_default_message(exc: BaseException) -> None:
    # No message given: the exception's own text is shown with every URL cut to scheme, host and path.
    failure = classify_http_exception(exc)
    assert CANARY not in str(failure) and QUERY not in str(failure)
    assert "https://llm.example/v1" in str(failure)


def test_error_summary_of_a_non_http_error_is_its_type_only() -> None:
    # An SDK error's text holds the provider's body.
    assert error_summary(RuntimeError(f"echo {CANARY}")) == "RuntimeError"
