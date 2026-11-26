#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The observability machine copies retry facts from a model return onto its end record."""

from __future__ import annotations

from types import SimpleNamespace

from mas.runtime.kernel.envelope import EnvelopeSymbol
from mas.runtime.machines.obs_envelope import ObsEnvelopeMachine


def _step(**extra) -> dict:
    seen: dict = {}
    event = SimpleNamespace(
        response_kind="MODEL_TEXT",
        text="ok",
        next_step="STOP",
        usage={},
        finish_reason="stop",
        tool_name="",
        **extra,
    )
    ctx = SimpleNamespace(
        observability=SimpleNamespace(
            record_engine_io_return=lambda **kw: seen.update(kw),
            record_envelope_activity=lambda **kw: None,
        ),
        contract=SimpleNamespace(value="llm"),
        operation="call",
        scheduled_op="LLM_CALL",
        tool_name="",
        correlation_id=7,
        ingress_event=event,
    )
    ObsEnvelopeMachine().step(EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE, ctx)
    return seen


def test_retry_attempts_and_failure_facts_reach_the_end_record() -> None:
    seen = _step(retry_attempts=2, failure_class="transient", failure_code="HTTP_429", truncation_retries=1)
    assert seen["retry_attempts"] == 2
    assert seen["failure_class"] == "transient"
    assert seen["failure_code"] == "HTTP_429"
    assert seen["truncation_retries"] == 1


def test_a_clean_return_carries_none_of_them() -> None:
    seen = _step()
    assert not {"retry_attempts", "failure_class", "failure_code"} & set(seen)
