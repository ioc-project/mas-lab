#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map transport / tool exceptions onto :class:`ClassifiedFailure`."""

from __future__ import annotations

import re
import ssl
import sys
from pathlib import Path
from typing import Any

from mas.runtime.reliability.classes import CircuitOpenError, ClassifiedFailure, FailureClass

_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
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


def classify_http_exception(exc: BaseException, *, message: str = "") -> ClassifiedFailure:
    """Classify an httpx/SSL/generic transport error."""
    import httpx

    text = str(exc)
    operator = message or f"request failed: {exc}"

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
    if isinstance(exc, httpx.HTTPStatusError):
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
    if status in (401, 403) and any(tok in lowered for tok in _BUDGET_MARKERS):
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code="BUDGET")
    if status in (401, 403):
        return ClassifiedFailure(operator, failure_class=FailureClass.FATAL, code=f"HTTP_{status}")
    if status in _RETRYABLE_STATUS or status >= 500:
        return ClassifiedFailure(operator, failure_class=FailureClass.TRANSIENT, code=f"HTTP_{status}")
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
    classified = classify_http_exception(exc, message=str(exc))
    if classified.failure_class in (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE, FailureClass.FATAL):
        return classified
    text = str(exc)
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

    if isinstance(exc, ssl.SSLError) or any(marker in str(exc) for marker in _TRANSIENT_MARKERS):
        return (
            "LLM request failed: TLS/connection dropped "
            f"({exc}). This is a transient socket error, not a certificate failure."
        )

    if isinstance(exc, httpx.ConnectError):
        exc_text = str(exc)
        if "Connection refused" in exc_text or "Name or service not known" in exc_text:
            return f"LLM request failed: cannot reach API ({exc}). Check api_base and network."
        return f"LLM request failed: connection error ({exc})."

    if isinstance(exc, httpx.TimeoutException):
        return "LLM request failed: request timed out. Retry or increase the client timeout."

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = _extract_api_error_message(exc.response)
        detail_hint = f": {detail}" if detail else ""
        if status in (401, 403):
            return (
                f"LLM request failed: HTTP {status} (authentication/authorization){detail_hint}. "
                "Check the API key env var named in your infra manifest (api_key_env). "
                "Ensure config.yaml infra_refs selects the correct bundle "
                "(e.g. standard:llm-proxy vs standard:production)."
            )
        if status == 429:
            return f"LLM request failed: HTTP 429 rate limit{detail_hint}."
        if status >= 500:
            return f"LLM request failed: HTTP {status} from LLM provider{detail_hint}."
        if "ExceededBudget" in detail or "budget_exceeded" in detail.lower():
            return (
                f"LLM request failed: proxy budget exceeded{detail_hint}. "
                "Contact your LLM proxy admin or use another infra bundle "
                "(e.g. mas-ctl chat --infra-ref standard:openai with OPENAI_API_KEY, "
                "or llm_cache replay with raise_on_miss for offline testing)."
            )
        return f"LLM request failed: HTTP {status}{detail_hint}"

    return f"LLM request failed: {exc}"


def classify_llm_failure(exc: BaseException) -> ClassifiedFailure:
    """Typed failure plus the operator-facing LLM HTTP message."""
    return classify_http_exception(exc, message=classify_llm_http_error(exc))


def is_retryable_llm_http_error(exc: BaseException) -> bool:
    """Transient / unavailable transport errors that a second POST can recover from."""
    if is_tls_verify_failure(exc):
        return False
    klass = classify_http_exception(exc).failure_class
    return klass in (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE)
