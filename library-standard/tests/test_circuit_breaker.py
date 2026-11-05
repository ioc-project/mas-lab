#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ThresholdCircuitBreaker plugin (engine I/O, not kernel)."""

from __future__ import annotations

import logging

import pytest
from mas.library.standard.plugins.reliability.circuit_breaker import ThresholdCircuitBreaker
from mas.runtime.reliability.classes import CircuitOpenError, FailureClass
from mas.runtime.reliability.policy import ReliabilitySettings
from mas.runtime.spec.gov import SpecBindingError


def test_threshold_breaker_trips_on_unavailable() -> None:
    breaker = ThresholdCircuitBreaker(failure_threshold=2, reset_timeout_s=30.0, on=["unavailable"])
    breaker.record_failure("tool:x", FailureClass.TRANSIENT)
    breaker.before_call("tool:x")
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
    with pytest.raises(CircuitOpenError):
        breaker.before_call("tool:x")


def test_absent_spec_is_disabled() -> None:
    settings = ReliabilitySettings.from_spec(None)
    assert settings.circuit_breaker is None


def test_enabled_false_is_disabled() -> None:
    settings = ReliabilitySettings.from_spec(
        {"control": {"circuit_breaker": {"enabled": False, "failure_threshold": 1}}}
    )
    assert settings.circuit_breaker is None


def test_yaml_on_boolean_key() -> None:
    settings = ReliabilitySettings.from_spec(
        {"control": {"circuit_breaker": {True: ["unavailable"], "failure_threshold": 2}}}
    )
    assert isinstance(settings.circuit_breaker, ThresholdCircuitBreaker)
    assert settings.circuit_breaker.on == (FailureClass.UNAVAILABLE,)


def test_from_spec_loads_plugin() -> None:
    settings = ReliabilitySettings.from_spec(
        {"control": {"circuit_breaker": {"failure_threshold": 2, "reset_timeout_s": 1, "on": ["unavailable"]}}}
    )
    assert isinstance(settings.circuit_breaker, ThresholdCircuitBreaker)
    assert settings.circuit_breaker.failure_threshold == 2


def test_unknown_field_rejected() -> None:
    with pytest.raises(SpecBindingError, match="unknown field"):
        ReliabilitySettings.from_spec({"control": {"circuit_breaker": {"retries": 3}}})


def test_circuit_open_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    breaker = ThresholdCircuitBreaker(failure_threshold=1, reset_timeout_s=30.0)
    with caplog.at_level(logging.WARNING, logger="mas.runtime.reliability"):
        breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
        with pytest.raises(CircuitOpenError):
            breaker.before_call("tool:x")
    assert any("circuit open" in r.getMessage() for r in caplog.records)
