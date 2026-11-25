#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The llm_scheduler layer lowers in-flight on a refusal, pauses after a streak, and recovers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.library.standard.plugins.infra.llm_scheduler import LlmSchedulerMiddleware, call_was_refused
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _RefuseThenOk:
    remaining: int = 1
    model: str = "gpt-test"

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return f"preview:{op}:{correlation_id}"

    def reset_turn_state(self) -> None:
        return None

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if self.remaining > 0:
            self.remaining -= 1
            raise ClassifiedFailure("throttled", failure_class=FailureClass.TRANSIENT, code="HTTP_429")
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text="ok",
        )


async def test_refusal_lowers_in_flight_and_clean_call_raises_it(tmp_path: Path) -> None:
    inner = _RefuseThenOk(remaining=1)
    params = {
        "max_in_flight": 4,
        "min_in_flight": 1,
        "decrease": 2,
        "increase": 1,
        "breaker_streak": 9,
        "cooldown_s": 0,
        "state_path": str(tmp_path / "sched.json"),
        "timeout_s": 5,
        "keys": ["model"],
    }
    engine = LlmSchedulerMiddleware.wrap(inner, params)
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    with pytest.raises(ClassifiedFailure):
        await engine.ainvoke(io)
    assert engine._effective == 2
    await engine.ainvoke(io)
    assert engine._effective == 3


def test_observe_opens_a_circuit_after_a_streak(tmp_path: Path) -> None:
    engine = LlmSchedulerMiddleware.wrap(
        _RefuseThenOk(remaining=0),
        {
            "max_in_flight": 4,
            "breaker_streak": 2,
            "cooldown_s": 30,
            "decrease": 2,
            "state_path": str(tmp_path / "sched.json"),
            "keys": ["model"],
        },
    )
    engine.observe(refused=True)
    engine.observe(refused=True)
    assert engine._effective == 0
    assert engine._paused_until > time.monotonic()


def test_call_was_refused_counts_only_refusals() -> None:
    assert not call_was_refused(result=SimpleNamespace(retry_attempts=2, failure_class="timeout", failure_code=""))
    assert call_was_refused(result=SimpleNamespace(retry_attempts=1, failure_class="rate_limit", failure_code=""))
    assert call_was_refused(result=SimpleNamespace(retry_attempts=0, failure_class="", failure_code="429"))
    assert not call_was_refused(result=SimpleNamespace(retry_attempts=0, failure_class="", failure_code=""))
    assert not call_was_refused()
    assert call_was_refused(exc=SimpleNamespace(code="HTTP_429"))
    assert (
        call_was_refused(exc=SimpleNamespace(code="", failure_class=FailureClass.TRANSIENT, failure_code="")) is False
    )
    assert call_was_refused(exc=SimpleNamespace(code="", failure_class="rate_limit"))


def test_param_defaults_and_floors(tmp_path: Path) -> None:
    engine = LlmSchedulerMiddleware.wrap(_RefuseThenOk(), {"max_in_flight": 4, "state_path": str(tmp_path / "s.json")})
    assert (engine.min_in_flight, engine.increase, engine.decrease, engine.breaker_streak) == (1, 1, 2.0, 3)
    floored = LlmSchedulerMiddleware.wrap(
        _RefuseThenOk(), {"max_in_flight": 4, "increase": 0, "decrease": 0.1, "state_path": str(tmp_path / "s.json")}
    )
    assert (floored.increase, floored.decrease) == (1, 1.0)


async def test_a_paused_leaf_waits_out_the_cooldown_then_resumes(tmp_path: Path) -> None:
    engine = LlmSchedulerMiddleware.wrap(
        _RefuseThenOk(remaining=0),
        {
            "max_in_flight": 2,
            "breaker_streak": 1,
            "cooldown_s": 0.05,
            "state_path": str(tmp_path / "s.json"),
            "timeout_s": 5,
            "keys": ["model"],
        },
    )
    engine.observe(refused=True)
    assert engine._effective == 0
    started = time.monotonic()
    await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert time.monotonic() - started >= 0.04
    assert engine._paused_until == 0.0 and engine._effective >= 1
