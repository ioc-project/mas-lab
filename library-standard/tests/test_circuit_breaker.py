# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""The circuit_breaker layer: the rules, and the state transitions it records."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pytest
from mas.library.standard.plugins.layers.circuit_breaker import BreakerPolicy, CircuitBreakerLayer
from mas.runtime.layers.state import MemorySpecPort
from mas.runtime.reliability.classes import CircuitOpenError, FailureClass
from mas.runtime.reliability.policy import ReliabilitySettings
from mas.runtime.session import SpecConflict


class _RecordingPort(MemorySpecPort):
    """A port that remembers every write, so a test can see when the layer records state."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, dict[str, Any] | None, str]] = []

    def swap(
        self, layer: str, target: str, expected: Mapping[str, Any], state: Mapping[str, Any] | None, *, reason: str
    ) -> None:
        super().swap(layer, target, expected, state, reason=reason)
        self.writes.append((target, dict(state) if state else None, reason))


def test_breaker_trips_on_unavailable() -> None:
    breaker = CircuitBreakerLayer(failure_threshold=2, reset_timeout_s=30.0, on=["unavailable"])
    breaker.record_failure("tool:x", FailureClass.TRANSIENT)
    breaker.before_call("tool:x")
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
    with pytest.raises(CircuitOpenError):
        breaker.before_call("tool:x")
    assert breaker.is_open("tool:x")


def test_absent_spec_adds_no_breaker() -> None:
    ReliabilitySettings.from_spec(None)
    ReliabilitySettings.from_spec({"control": {"circuit_breaker": None}})


def test_enabled_false_never_opens() -> None:
    breaker = CircuitBreakerLayer(enabled=False, failure_threshold=1)
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
    breaker.before_call("tool:x")
    assert not breaker.enabled


def test_yaml_on_boolean_key() -> None:
    policy = BreakerPolicy.from_mapping({True: ["unavailable"], "failure_threshold": 2})
    assert policy.on == (FailureClass.UNAVAILABLE,)
    assert policy.failure_threshold == 2


def test_bad_params_are_refused_when_the_layer_is_built() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        CircuitBreakerLayer(retries=3)
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreakerLayer(failure_threshold=0)
    with pytest.raises(ValueError, match="unknown failure class"):
        CircuitBreakerLayer(on=["gone"])
    with pytest.raises(ValueError, match="reset_timeout_s"):
        CircuitBreakerLayer(reset_timeout_s=-1)


def test_circuit_open_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    breaker = CircuitBreakerLayer(failure_threshold=1, reset_timeout_s=30.0)
    with caplog.at_level(logging.WARNING, logger="mas.runtime.reliability"):
        breaker.record_failure("tool:x", FailureClass.UNAVAILABLE)
        with pytest.raises(CircuitOpenError):
            breaker.before_call("tool:x")
    assert any("circuit open" in r.getMessage() for r in caplog.records)


def test_state_is_recorded_on_every_change_and_never_while_healthy() -> None:
    now = {"t": 1000.0}
    port = _RecordingPort()
    breaker = CircuitBreakerLayer(failure_threshold=2, reset_timeout_s=30.0, clock=lambda: now["t"])
    breaker.before_call("llm:x", port)
    breaker.record_success("llm:x", port)
    assert port.writes == []  # a healthy circuit costs no write
    breaker.record_failure("llm:x", FailureClass.UNAVAILABLE, port)
    assert port.read("circuit_breaker", "llm:x") == {"failures": 1}
    breaker.record_failure("llm:x", FailureClass.UNAVAILABLE, port)
    assert port.read("circuit_breaker", "llm:x") == {"failures": 2, "opened_at": 1000.0}
    assert "circuit_breaker opened llm:x" in port.writes[-1][2]
    with pytest.raises(CircuitOpenError):
        breaker.before_call("llm:x", port)
    now["t"] = 1031.0
    breaker.before_call("llm:x", port)  # half-open: one probe is let through
    assert port.read("circuit_breaker", "llm:x")["half_open"] is True
    breaker.record_failure("llm:x", FailureClass.UNAVAILABLE, port)  # the probe failed: open again
    assert port.read("circuit_breaker", "llm:x") == {"failures": 2, "opened_at": 1031.0, "half_open": False}
    now["t"] = 1070.0
    breaker.before_call("llm:x", port)
    breaker.record_success("llm:x", port)
    assert port.read("circuit_breaker", "llm:x") == {}
    assert port.writes[-1][2] == "circuit_breaker closed llm:x"


def test_a_state_in_the_port_governs_a_fresh_layer() -> None:
    """The state is not the layer's: a new layer over the same port sees the circuit open."""
    port = _RecordingPort()
    port.swap("circuit_breaker", "tool:x", {}, {"failures": 3, "opened_at": 500.0}, reason="seed")
    layer = CircuitBreakerLayer(failure_threshold=3, reset_timeout_s=30.0, clock=lambda: 510.0)
    with pytest.raises(CircuitOpenError):
        layer.before_call("tool:x", port)
    assert layer.is_open("tool:x", port)
    assert not layer.is_open("tool:y", port)


class _Racing(MemorySpecPort):
    """A port where another writer gets in first once, so the layer's compare-and-set must lose and retry."""

    def __init__(self) -> None:
        super().__init__()
        self.raced = False

    def swap(
        self, layer: str, target: str, expected: Mapping[str, Any], state: Mapping[str, Any] | None, *, reason: str
    ) -> None:
        if not self.raced:
            self.raced = True
            super().swap(layer, target, expected, {"failures": 1}, reason="other writer")
        super().swap(layer, target, expected, state, reason=reason)


def test_a_lost_compare_and_set_is_recomputed_from_the_new_state() -> None:
    port = _Racing()
    breaker = CircuitBreakerLayer(failure_threshold=3, reset_timeout_s=30.0)
    breaker.record_failure("tool:x", FailureClass.UNAVAILABLE, port)
    # the other writer made it 1; this failure then made it 2, not a lost update back to 1
    assert port.read("circuit_breaker", "tool:x") == {"failures": 2}


def test_memory_port_refuses_a_stale_swap() -> None:
    port = MemorySpecPort()
    port.swap("l", "t", {}, {"a": 1}, reason="r")
    with pytest.raises(SpecConflict):
        port.swap("l", "t", {}, {"a": 2}, reason="r")


def test_only_one_call_is_let_through_as_the_probe() -> None:
    now = {"t": 1000.0}
    port = _RecordingPort()
    breaker = CircuitBreakerLayer(failure_threshold=1, reset_timeout_s=30.0, clock=lambda: now["t"])
    breaker.record_failure("llm:x", FailureClass.UNAVAILABLE, port)
    now["t"] = 1031.0
    breaker.before_call("llm:x", port)  # the probe
    assert port.read("circuit_breaker", "llm:x")["probe_at"] == 1031.0
    with pytest.raises(CircuitOpenError):
        breaker.before_call("llm:x", port)  # a second caller waits for the probe
    assert breaker.is_open("llm:x", port)
    breaker.record_success("llm:x", port)
    breaker.before_call("llm:x", port)  # closed again


def test_a_probe_that_never_reports_is_replaced_after_another_timeout() -> None:
    now = {"t": 1000.0}
    port = _RecordingPort()
    breaker = CircuitBreakerLayer(failure_threshold=1, reset_timeout_s=30.0, clock=lambda: now["t"])
    breaker.record_failure("llm:x", FailureClass.UNAVAILABLE, port)
    now["t"] = 1031.0
    breaker.before_call("llm:x", port)
    now["t"] = 1062.0
    breaker.before_call("llm:x", port)  # the first probe is lost: another is let through
    assert port.read("circuit_breaker", "llm:x")["probe_at"] == 1062.0
