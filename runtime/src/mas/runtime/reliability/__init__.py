#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed failures and their classification, shared by the engine and the layers.

Retry and the circuit breaker are layer plugins; their policy and loop live in the library that registers them.
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

__all__ = [
    "CircuitOpenError",
    "ClassifiedFailure",
    "FailureClass",
    "RELIABILITY_LOGGER",
    "classify_http_exception",
    "classify_llm_failure",
    "classify_llm_http_error",
    "classify_tool_exception",
    "is_retryable_llm_http_error",
    "is_tls_verify_failure",
]
