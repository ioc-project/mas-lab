#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Server Retry-After hints and CONTEXT_LENGTH_EXCEEDED classification (offline)."""

from __future__ import annotations

import email.utils
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import (
    classify_http_exception,
    classify_llm_failure,
    is_context_length_exceeded,
    is_retryable_llm_http_error,
    retry_after_seconds,
)

_URL = "https://llm.example.test/v1/chat/completions"


def _response(
    status: int, *, headers: dict[str, str] | None = None, json_body: Any = None, text: str = ""
) -> httpx.Response:
    request = httpx.Request("POST", _URL)
    if json_body is not None:
        return httpx.Response(status, json=json_body, headers=headers, request=request)
    return httpx.Response(status, text=text, headers=headers, request=request)


def _status_error(status: int, **kwargs: Any) -> httpx.HTTPStatusError:
    response = _response(status, **kwargs)
    return httpx.HTTPStatusError(f"HTTP {status}", request=response.request, response=response)


def _retry_info(delay: str) -> dict[str, Any]:
    return {
        "error": {
            "code": 429,
            "message": "Resource exhausted",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}],
        }
    }


# --- Retry-After: headers -------------------------------------------------------


def test_retry_after_ms_header_is_milliseconds() -> None:
    assert retry_after_seconds(_response(429, headers={"retry-after-ms": "1500"})) == 1.5


def test_retry_after_seconds_integer_and_float() -> None:
    assert retry_after_seconds(_response(429, headers={"retry-after": "3"})) == 3.0
    assert retry_after_seconds(_response(429, headers={"retry-after": "1.5"})) == 1.5


def test_retry_after_http_date_is_relative_to_now() -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=90)
    value = retry_after_seconds(_response(503, headers={"retry-after": email.utils.format_datetime(when, usegmt=True)}))
    assert value is not None
    assert value == pytest.approx(90.0, abs=5.0)


def test_retry_after_http_date_in_the_past_is_zero() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    value = retry_after_seconds(_response(503, headers={"retry-after": email.utils.format_datetime(past, usegmt=True)}))
    assert value == 0.0


def test_retry_after_negative_clamps_to_zero() -> None:
    assert retry_after_seconds(_response(429, headers={"retry-after": "-5"})) == 0.0
    assert retry_after_seconds(_response(429, headers={"retry-after-ms": "-250"})) == 0.0


def test_retry_after_ms_wins_over_seconds_header() -> None:
    headers = {"retry-after-ms": "250", "retry-after": "9"}
    assert retry_after_seconds(_response(429, headers=headers)) == 0.25


@pytest.mark.parametrize("raw", ["soon", "", "nan", "inf", "Wed, not-a-date"])
def test_retry_after_malformed_header_is_none(raw: str) -> None:
    assert retry_after_seconds(_response(429, headers={"retry-after": raw})) is None


def test_retry_after_malformed_ms_falls_back_to_seconds_header() -> None:
    headers = {"retry-after-ms": "lots", "retry-after": "4"}
    assert retry_after_seconds(_response(429, headers=headers)) == 4.0


def test_retry_after_absent_is_none() -> None:
    assert retry_after_seconds(_response(429)) is None


# --- Retry-After: Google RPC RetryInfo body -------------------------------------


@pytest.mark.parametrize(("delay", "expected"), [("3s", 3.0), ("1.5s", 1.5), (" 2 s ", 2.0), ("-4s", 0.0)])
def test_retry_info_body_delay(delay: str, expected: float) -> None:
    assert retry_after_seconds(_response(429, json_body=_retry_info(delay))) == expected


@pytest.mark.parametrize("delay", ["soon", "3", "3 minutes", ""])
def test_retry_info_body_malformed_delay_is_none(delay: str) -> None:
    assert retry_after_seconds(_response(429, json_body=_retry_info(delay))) is None


def test_retry_info_body_ignores_other_detail_types() -> None:
    body = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []},
                {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
            ]
        }
    }
    assert retry_after_seconds(_response(429, json_body=body)) is None


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"details": "not-a-list"}},
        {"error": "boom"},
        ["not", "an", "object"],
        {"error": {"details": [None, 7, {"@type": "x/RetryInfo"}]}},
    ],
)
def test_retry_info_body_wrong_shape_is_none(body: Any) -> None:
    assert retry_after_seconds(_response(429, json_body=body)) is None


def test_retry_after_non_json_body_is_none() -> None:
    assert retry_after_seconds(_response(503, text="<html>busy</html>")) is None


def test_retry_after_never_raises_on_odd_input() -> None:
    assert retry_after_seconds(None) is None
    assert retry_after_seconds(object()) is None


# --- Retry-After on classified failures -----------------------------------------


def test_429_and_5xx_carry_retry_after_on_classified_failure() -> None:
    for status in (429, 500, 502, 503, 504, 529):
        classified = classify_http_exception(_status_error(status, headers={"retry-after": "3"}))
        assert classified.failure_class is FailureClass.TRANSIENT, status
        assert classified.retry_after_s == 3.0, status


def test_retry_info_body_on_503_sets_retry_after() -> None:
    classified = classify_http_exception(_status_error(503, json_body=_retry_info("1.5s")))
    assert classified.retry_after_s == 1.5


def test_non_throttle_status_has_no_retry_after() -> None:
    classified = classify_http_exception(_status_error(400, headers={"retry-after": "3"}))
    assert classified.retry_after_s is None


def test_classified_failure_keeps_retry_after_through_with_attempts() -> None:
    failure = ClassifiedFailure("x", failure_class=FailureClass.TRANSIENT, code="HTTP_429", retry_after_s=2.5)
    assert failure.retry_after_s == 2.5
    again = failure.with_attempts(3)
    assert again.attempts == 3
    assert again.retry_after_s == 2.5
    assert ClassifiedFailure("y", failure_class=FailureClass.TRANSIENT).retry_after_s is None


# --- Context-length phrases -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "error code: context_length_exceeded",
        "This model's MAXIMUM CONTEXT LENGTH is 8192 tokens",
        "prompt is too long: 250000 tokens > 200000 maximum",
        "Input is too long for requested model",
        "too many input tokens for this request",
        "The request exceeds the maximum number of tokens allowed",
        "input token count 9000 exceeds the limit",
        "Request too large for model: 12000 tokens",
    ],
)
def test_context_length_phrases_match(text: str) -> None:
    assert is_context_length_exceeded(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "rate limit exceeded",
        "input token count is 42",  # no "exceeds"
        "request too large",  # no "tokens"
        "",
    ],
)
def test_context_length_unrelated_text_does_not_match(text: str) -> None:
    assert is_context_length_exceeded(text) is False


def test_context_length_accepts_none_and_plain_exceptions() -> None:
    assert is_context_length_exceeded(None) is False
    assert is_context_length_exceeded(RuntimeError("prompt is too long")) is True
    assert is_context_length_exceeded(RuntimeError("timeout")) is False


# --- Context-length classification ----------------------------------------------


_CONTEXT_BODY = {
    "error": {
        "type": "invalid_request_error",
        "code": "context_length_exceeded",
        "message": "This model's maximum context length is 8192 tokens. However, you requested 9000.",
    }
}


def test_400_context_length_is_application_context_code() -> None:
    classified = classify_http_exception(_status_error(400, json_body=_CONTEXT_BODY))
    assert classified.failure_class is FailureClass.APPLICATION
    assert classified.code == "CONTEXT_LENGTH_EXCEEDED"


def test_413_request_too_large_is_application_context_code() -> None:
    body = {"error": {"message": "Request too large: requested 12000 tokens"}}
    classified = classify_http_exception(_status_error(413, json_body=body))
    assert classified.failure_class is FailureClass.APPLICATION
    assert classified.code == "CONTEXT_LENGTH_EXCEEDED"


def test_context_length_is_never_retryable() -> None:
    exc = _status_error(400, json_body=_CONTEXT_BODY)
    assert is_retryable_llm_http_error(exc) is False


def test_400_without_context_phrase_keeps_http_code() -> None:
    classified = classify_http_exception(_status_error(400, json_body={"error": {"message": "bad field"}}))
    assert classified.failure_class is FailureClass.APPLICATION
    assert classified.code == "HTTP_400"


def test_context_phrase_on_500_is_not_context_code() -> None:
    classified = classify_http_exception(_status_error(500, json_body=_CONTEXT_BODY))
    assert classified.code == "HTTP_500"


def test_llm_operator_message_for_context_length() -> None:
    classified = classify_llm_failure(_status_error(400, json_body=_CONTEXT_BODY))
    assert classified.code == "CONTEXT_LENGTH_EXCEEDED"
    assert str(classified).startswith("LLM request failed: the prompt does not fit the model context window")
    assert "maximum context length" in str(classified)
    assert "context_window" in str(classified)
    assert "context_window" in str(classified)


def test_llm_429_keeps_retry_after_through_operator_message() -> None:
    classified = classify_llm_failure(_status_error(429, headers={"retry-after-ms": "2000"}))
    assert classified.failure_class is FailureClass.TRANSIENT
    assert classified.retry_after_s == 2.0
