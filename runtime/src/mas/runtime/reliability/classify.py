#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map transport / tool exceptions onto :class:`ClassifiedFailure`."""

from __future__ import annotations

import email.utils
import math
import re
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass

_TRANSIENT_MARKERS = (
    "UNEXPECTED_EOF",
    "UNEXPECTED_EOF_WHILE_READING",
    "Connection reset",
    "Connection aborted",
    "ECONNRESET",
    "Server disconnected",
    "Remote end closed connection",
    "broken pipe",
)
_UNAVAILABLE_MARKERS = (
    "Connection refused",
    "Name or service not known",
    "nodename nor servname",
    "Temporary failure in name resolution",
    "Network is unreachable",
)
_BUDGET_MARKERS = ("exceededbudget", "budget_exceeded", "budget", "quota")
_CONTEXT_LENGTH_STATUS = frozenset({400, 413})
_CONTEXT_LENGTH_PHRASES = (
    "context_length_exceeded",
    "maximum context length",
    "prompt is too long",
    "input is too long",
    "too many input tokens",
    "exceeds the maximum number of tokens",
)
_RETRY_DELAY_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*s\s*$", re.IGNORECASE)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")


def is_retryable_status(status: int) -> bool:
    """429 and any 5xx (including 500 from an overloaded LLM server): the call may succeed when re-issued."""
    return status == 429 or status >= 500


def shown_urls(text: str) -> str:
    """``text`` with every url in it cut to scheme, host, port and path: no userinfo, query or fragment."""
    from urllib.parse import urlsplit, urlunsplit

    def cut(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group(0))
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            netloc = f"{host}:{parsed.port}" if parsed.port else host
        except ValueError:
            return "<url>"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    return _URL_RE.sub(cut, str(text))


def response_status(exc: BaseException) -> int | None:
    """The HTTP status of the response ``exc`` carries (httpx, the OpenAI and Anthropic SDKs), else None."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def provider_failure(
    family: str, *, status: int | None = None, error: Any = None, headers: Any = None
) -> ClassifiedFailure:
    """A failure a provider reported in an answer it sent with HTTP 200 (a JSON ``error`` or a stream error
    event): ``<family> call failed[: HTTP <status>]``. ``error`` and ``headers`` are never read or shown."""
    transient = status is not None and is_retryable_status(status)
    return ClassifiedFailure(
        f"{family} call failed" + (f": HTTP {status}" if status else ""),
        failure_class=FailureClass.TRANSIENT if transient else FailureClass.APPLICATION,
        code=f"HTTP_{status}" if status else "PROVIDER_ERROR",
    )


def error_summary(exc: BaseException) -> str:
    """The exception's type name, plus ``HTTP <status>`` when it carries a response. Never its text: an SDK
    error's text holds the provider's body, which may echo the request."""
    status = response_status(exc)
    return type(exc).__name__ + (f": HTTP {status}" if status is not None else "")


def is_rate_limited(exc: BaseException) -> bool:
    """True for a 429 response or an SDK ``RateLimitError``; the error's text is never read."""
    return response_status(exc) == 429 or any(cls.__name__ == "RateLimitError" for cls in type(exc).__mro__)


def safe_exception_text(exc: BaseException) -> str:
    """What an error may say about ``exc``: one with a response is :func:`error_summary` (never its body),
    anything else its text with urls cut by :func:`shown_urls`."""
    if response_status(exc) is not None:
        return error_summary(exc)
    return shown_urls(str(exc))


def _iter_exception_chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def is_tls_verify_failure(exc: BaseException) -> bool:
    """True only for certificate verification failures, not dropped TLS sockets."""
    for err in _iter_exception_chain(exc):
        if isinstance(err, ssl.SSLCertVerificationError):
            return True
        text = str(err)
        if any(
            marker in text
            for marker in (
                "CERTIFICATE_VERIFY_FAILED",
                "certificate verify failed",
                "SSL: CERTIFICATE",
            )
        ):
            return True
    return False


def _finite_seconds(value: float) -> float | None:
    """A non-negative finite number of seconds, or None (negative clamps to 0)."""
    if not math.isfinite(value):
        return None
    return max(0.0, value)


def _parse_retry_after_date(text: str) -> float | None:
    """HTTP-date form of Retry-After: seconds from now until that instant."""
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return _finite_seconds((when - datetime.now(timezone.utc)).total_seconds())


def _parse_retry_info_body(response: Any) -> float | None:
    """Google RPC ``error.details[]`` entry whose ``@type`` ends with ``RetryInfo``."""
    data = response.json()
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    details = err.get("details") if isinstance(err, dict) else None
    if not isinstance(details, list):
        return None
    for item in details:
        if not isinstance(item, dict):
            continue
        kind = item.get("@type")
        delay = item.get("retryDelay")
        if not isinstance(kind, str) or not kind.endswith("RetryInfo") or not isinstance(delay, str):
            continue
        match = _RETRY_DELAY_RE.match(delay)
        if match is None:
            continue
        seconds = _finite_seconds(float(match.group(1)))
        if seconds is not None:
            return seconds
    return None


def retry_after_seconds(response: Any) -> float | None:
    """Server-requested wait in seconds, or None. Never raises.

    Looks at ``retry-after-ms`` (milliseconds), then ``retry-after`` (seconds or
    an HTTP-date), then a Google RPC ``RetryInfo`` body hint. A malformed value
    is skipped and the next source is tried.
    """
    try:
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw_ms = headers.get("retry-after-ms")
            if raw_ms is not None:
                number = _parse_number(raw_ms)
                if number is not None:
                    seconds = _finite_seconds(number / 1000.0)
                    if seconds is not None:
                        return seconds
            raw = headers.get("retry-after")
            if raw is not None:
                number = _parse_number(raw)
                seconds = _finite_seconds(number) if number is not None else _parse_retry_after_date(raw.strip())
                if seconds is not None:
                    return seconds
        return _parse_retry_info_body(response)
    except Exception:
        return None


def _parse_number(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return None


def _is_context_length_text(text: str) -> bool:
    lowered = text.lower()
    if any(phrase in lowered for phrase in _CONTEXT_LENGTH_PHRASES):
        return True
    if "input token count" in lowered and "exceeds" in lowered:
        return True
    return "request too large" in lowered and "tokens" in lowered


def is_context_length_exceeded(text_or_exc: str | BaseException | None) -> bool:
    """True when a message (or an exception's text / API error body) says the prompt is too long."""
    if text_or_exc is None:
        return False
    if isinstance(text_or_exc, str):
        return _is_context_length_text(text_or_exc)
    if not isinstance(text_or_exc, BaseException):
        return False
    texts = [str(text_or_exc)]
    response = getattr(text_or_exc, "response", None)
    if response is not None:
        detail = _extract_api_error_message(response)
        if detail:
            texts.append(detail)
    return any(_is_context_length_text(text) for text in texts)


def classify_http_exception(exc: BaseException, *, message: str = "") -> ClassifiedFailure:
    """Classify an httpx/SSL/generic transport error."""
    import httpx

    text = str(exc)
    operator = message or f"request failed: {safe_exception_text(exc)}"

    if isinstance(exc, ClassifiedFailure):
        return exc
    if is_tls_verify_failure(exc):
        return ClassifiedFailure(operator, failure_class=FailureClass.FATAL, code="TLS_VERIFY")
    if isinstance(exc, httpx.ConnectError):
        klass = (
            FailureClass.UNAVAILABLE
            if any(marker in text for marker in _UNAVAILABLE_MARKERS)
            else FailureClass.TRANSIENT
        )
        return ClassifiedFailure(operator, failure_class=klass, code="CONNECT")
    if isinstance(exc, httpx.TimeoutException):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="TIMEOUT")
    if isinstance(
        exc,
        (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout),
    ):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="TRANSPORT")
    if isinstance(exc, ssl.SSLError):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="TLS_DROP")
    if response_status(exc) is not None:
        return _classify_status(exc, operator=operator)
    lowered = text.lower()
    if re.search(r"\b429\b", lowered) or any(tok in lowered for tok in ("rate limit", "too many", "quota")):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="RATE_LIMIT")
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return ClassifiedFailure(operator, failure_class=FailureClass.UNAVAILABLE, code="CONNECT")
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="TRANSPORT")
    return ClassifiedFailure(operator, failure_class=FailureClass.APPLICATION, code="ERROR")


def _classify_status(exc: Any, *, operator: str) -> ClassifiedFailure:
    status = exc.response.status_code
    body = ""
    with __import__("contextlib").suppress(Exception):
        body = (exc.response.text or "")[:500]
    lowered = (body + " " + str(exc)).lower()
    if status in _CONTEXT_LENGTH_STATUS and is_context_length_exceeded(exc):
        # An application error: the same prompt will fail again, so it is never retried.
        return ClassifiedFailure(operator, failure_class=FailureClass.APPLICATION, code="CONTEXT_LENGTH_EXCEEDED")
    if status in (401, 403) and any(tok in lowered for tok in _BUDGET_MARKERS):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="BUDGET")
    if status in (401, 403):
        return ClassifiedFailure(operator, failure_class=FailureClass.FATAL, code=f"HTTP_{status}")
    if is_retryable_status(status):
        return ClassifiedFailure(
            operator,
            failure_class=FailureClass.TRANSIENT,
            code=f"HTTP_{status}",
            retry_after_s=retry_after_seconds(exc.response),
        )
    if 400 <= status < 500:
        return ClassifiedFailure(operator, failure_class=FailureClass.APPLICATION, code=f"HTTP_{status}")
    return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code=f"HTTP_{status}")


def classify_tool_exception(exc: BaseException) -> ClassifiedFailure:
    """Tool dispatch / implementation failure."""
    from mas.runtime.boundary.control.contract import ControlDenied, SessionPaused
    from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError
    from mas.runtime.engine.tool_routing import ExplicitToolUnavailableError, UnclaimedToolError

    if isinstance(exc, (ClassifiedFailure, CircuitOpenError)):
        return exc
    if isinstance(exc, (SessionPaused, ControlDenied)):
        # A refusal by the session, not a tool fault: never retried, whatever its text says.
        return ClassifiedFailure(str(exc), failure_class=FailureClass.APPLICATION, code="CONTROL")
    if type(exc).__name__ == "ToolExecutionError" and exc.__cause__ is not None:
        return classify_tool_exception(exc.__cause__)
    if response_status(exc) is not None:
        return classify_http_exception(exc, message=f"tool call failed: {error_summary(exc)}")
    if isinstance(exc, (ManifestToolLoadError, UnclaimedToolError)):
        return ClassifiedFailure(
            str(exc),
            failure_class=FailureClass.APPLICATION,
            code="TOOL_UNKNOWN",
        )
    if isinstance(exc, ExplicitToolUnavailableError):
        return ClassifiedFailure(
            str(exc),
            failure_class=FailureClass.UNAVAILABLE,
            code="TOOL_UNAVAILABLE",
        )
    text = shown_urls(str(exc))
    classified = classify_http_exception(exc, message=text)
    if classified.failure_class in (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE, FailureClass.FATAL):
        return classified
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return ClassifiedFailure(text, failure_class=FailureClass.UNAVAILABLE, code="TOOL_UNAVAILABLE")
    if "timeout" in text.lower() or any(marker in text for marker in _TRANSIENT_MARKERS):
        return ClassifiedFailure(text, failure_class=FailureClass.TRANSIENT, code="TOOL_TRANSIENT")
    return ClassifiedFailure(text, failure_class=FailureClass.APPLICATION, code="TOOL_ERROR")


def _macos_install_certificates_hint() -> str:
    for base in (Path(sys.prefix), Path(sys.base_prefix)):
        cmd = base / "Install Certificates.command"
        if cmd.is_file():
            return f"Run: {cmd}"
    return "Run Install Certificates.command from your Python folder."


def _extract_api_error_message(response: Any) -> str:
    """The provider's error message, for matching only (context length, budget). Never shown."""
    with __import__("contextlib").suppress(Exception):
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            msg = data.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    with __import__("contextlib").suppress(Exception):
        text = response.text.strip()
        if text:
            return text[:500]
    return ""


def classify_llm_http_error(exc: BaseException) -> str:
    """Map transport/HTTP failures to a short, actionable operator message."""
    import httpx

    if is_tls_verify_failure(exc):
        return (
            "LLM request failed: TLS certificate verification failed. "
            "Use the repo .venv (`task install-dev`, `direnv allow`). "
            "For corporate proxies set SSL_CERT_FILE or spec.infra llm_proxy.ca_bundle. "
            f"On macOS CPython you may also {_macos_install_certificates_hint()}."
        )

    status = response_status(exc)
    if status is not None:
        # The body is read to recognise the failure, never copied: a proxy may echo the refused request.
        shown = f"HTTP {status}"
        if status in _CONTEXT_LENGTH_STATUS and is_context_length_exceeded(exc):
            return (
                f"LLM request failed: the prompt does not fit the model context window ({shown}). "
                "Set context_window to the model's real limit so history is trimmed, "
                "or use a model with a larger window."
            )
        if status in (401, 403):
            return (
                f"LLM request failed: {shown}. "
                "Check the SecretRef on api_key in your infra manifest. "
                "Ensure config.yaml infra_refs selects the correct bundle "
                "(e.g. standard:llm-proxy vs standard:production)."
            )
        return f"LLM request failed: {shown}."

    if isinstance(exc, ssl.SSLError) or any(marker in str(exc) for marker in _TRANSIENT_MARKERS):
        return (
            "LLM request failed: TLS/connection dropped "
            f"({shown_urls(str(exc))}). This is a transient socket error, not a certificate failure."
        )

    if isinstance(exc, httpx.ConnectError):
        exc_text = shown_urls(str(exc))
        if "Connection refused" in exc_text or "Name or service not known" in exc_text:
            return f"LLM request failed: cannot reach API ({exc_text}). Check api_base and network."
        return f"LLM request failed: connection error ({exc_text})."

    if isinstance(exc, httpx.TimeoutException):
        return "LLM request failed: request timed out. Retry or increase the client timeout."

    return f"LLM request failed: {safe_exception_text(exc)}"


def classify_llm_failure(exc: BaseException) -> ClassifiedFailure:
    """Typed failure plus the operator-facing LLM HTTP message."""
    return classify_http_exception(exc, message=classify_llm_http_error(exc))


def is_retryable_llm_http_error(exc: BaseException) -> bool:
    """Transient / unavailable transport errors that a second POST can recover from."""
    if is_tls_verify_failure(exc):
        return False
    klass = classify_http_exception(exc).failure_class
    return klass in (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE)
