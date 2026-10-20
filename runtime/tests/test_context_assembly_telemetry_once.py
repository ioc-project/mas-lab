#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One LLM call reports its context assembly once, however often it is assembled."""

from __future__ import annotations

from typing import Any

from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.driver.mocks import AutoCtxAssembler


class _Obs:
    def __init__(self) -> None:
        self.assembled: list[dict[str, Any]] = []

    def record_context_assembled(self, **kwargs: Any) -> None:
        self.assembled.append(kwargs)


def _ctx(obs: _Obs, text: str = "hi") -> AutoCtxAssembler:
    return AutoCtxAssembler(last_user_text=text, observability=obs)


def test_same_correlation_id_and_messages_is_recorded_once() -> None:
    obs = _Obs()
    ctx = _ctx(obs)
    first = assemble_llm_messages(ctx, correlation_id=3)
    second = assemble_llm_messages(ctx, correlation_id=3)
    assert first == second
    assert [e["correlation_id"] for e in obs.assembled] == [3]
    assert ctx.last_context_usage is not None


def test_cache_hit_preview_is_the_only_assembly_and_is_recorded() -> None:
    obs = _Obs()
    ctx = _ctx(obs)
    ctx._assembly_correlation_id = 7
    assemble_llm_messages(ctx)  # preview only: the cache answered, no live call
    assert [e["correlation_id"] for e in obs.assembled] == [7]


def test_preview_then_live_call_records_once() -> None:
    obs = _Obs()
    ctx = _ctx(obs)
    ctx._assembly_correlation_id = 4
    for _ in range(3):  # exchange snapshot, cache-key preview, live call
        assemble_llm_messages(ctx)
    assert len(obs.assembled) == 1


def test_different_messages_for_same_correlation_id_are_a_new_assembly() -> None:
    obs = _Obs()
    ctx = _ctx(obs, "one")
    assemble_llm_messages(ctx, correlation_id=5)
    ctx.last_user_text = "two"
    assemble_llm_messages(ctx, correlation_id=5)
    assert len(obs.assembled) == 2


def test_next_correlation_id_is_recorded_again() -> None:
    obs = _Obs()
    ctx = _ctx(obs)
    assemble_llm_messages(ctx, correlation_id=1)
    assemble_llm_messages(ctx, correlation_id=2)
    assert [e["correlation_id"] for e in obs.assembled] == [1, 2]
