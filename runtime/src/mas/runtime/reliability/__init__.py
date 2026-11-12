#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared reliability helpers for LLM HTTP and tool dispatch.

The circuit breaker is a layer plugin (``circuit_breaker``). Ingress ``error_policy`` lives on ``retry_on_error``.
"""

from mas.runtime.reliability.classes import (
    CircuitOpenError,
    ClassifiedFailure,
    FailureClass,
)
from mas.runtime.reliability.classify import (
    classify_http_exception,
    classify_llm_failure,
    classify_llm_http_error,
    classify_tool_exception,
    is_retryable_llm_http_error,
    is_tls_verify_failure,
)
from mas.runtime.reliability.log import LOGGER as RELIABILITY_LOGGER
from mas.runtime.reliability.policy import (
    ReliabilitySettings,
    RetryPolicy,
    llm_retry_policy,
)
from mas.runtime.reliability.retry import acall_with_retry, call_with_retry

__all__ = [
    "CircuitOpenError",
    "ClassifiedFailure",
    "FailureClass",
    "RELIABILITY_LOGGER",
    "ReliabilitySettings",
    "RetryPolicy",
    "acall_with_retry",
    "call_with_retry",
    "classify_http_exception",
    "classify_llm_failure",
    "classify_llm_http_error",
    "classify_tool_exception",
    "is_retryable_llm_http_error",
    "is_tls_verify_failure",
    "llm_retry_policy",
]
