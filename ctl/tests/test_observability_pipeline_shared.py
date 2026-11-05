#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared observability plugin set lifecycle for multi-agent MAS runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.session.observability import create_shared_observability
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.plugins import ObsPluginSet, build_observability_plugins
from mas.runtime.schema.observability import ObsEventKind


@dataclass
class _FakeDriver:
    observability: ObservabilityOperator = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)
    obs_plugin_set: object | None = None


def test_shared_plugin_set_close_emits_mas_call_end(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="agent")
    shared_set = ObsPluginSet(plugins=plugins)
    op_a = ObservabilityOperator()
    op_b = ObservabilityOperator()
    shared_set.subscribe_to(op_a, agent_id="agent-a")
    shared_set.subscribe_to(op_b, agent_id="agent-b")
    shared_set.begin_run(op_a)
    shared_set.close()

    events_path = tmp_path / "events.jsonl"
    kinds = [json.loads(line)["kind"] for line in events_path.read_text().splitlines() if line.strip()]
    assert kinds.count("mas_call_start") == 1
    assert kinds.count("mas_call_end") == 1


def test_subscribe_to_is_idempotent(tmp_path) -> None:
    binding = ObservabilityBinding(plugins=["native"])
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="agent")
    shared_set = ObsPluginSet(plugins=plugins)
    op = ObservabilityOperator()
    shared_set.subscribe_to(op, agent_id="a")
    shared_set.subscribe_to(op, agent_id="a")  # second call should not double-subscribe
    assert len(op._subscribers) == len(shared_set.plugins)


def test_create_shared_observability_begin_run_on_first_setup(tmp_path) -> None:
    config = ObservabilityConfig(enabled=True, plugins=["native"], events_file="events.jsonl", agent_id="entry")
    shared_set, setup = create_shared_observability(config, base_dir=tmp_path)
    assert shared_set is not None and setup is not None

    inst_a = _FakeInstance()
    inst_b = _FakeInstance()
    rec_a = setup(inst_a, "agent-a")
    rec_b = setup(inst_b, "agent-b")

    # begin_run should have fired exactly once
    assert shared_set._run_started

    # Recorders reference the shared set but don't own it
    assert rec_a is not None and not rec_a.owns_plugin_set
    assert rec_b is not None and not rec_b.owns_plugin_set

    # Closing recorder does not close the shared set
    rec_a.close()
    assert not shared_set._closed

    shared_set.close()
    assert shared_set._closed
    shared_set.close()  # idempotent — no crash


def test_sh_user_input_updates_turn_context() -> None:
    """_sh_user_input should propagate turn_id and exec_call_id into context."""
    transform = NativeObservabilityTransform()
    ctx = TransformContext(agent_id="planner", run_id="run-1")
    ctx._seen_engine_ops.add((1, "LLM_CALL"))

    out = transform.transform(
        {
            "_source": "session",
            "session_kind": "user_input",
            "text": "hi",
            "call_id": "planner-u2-exec",
            "turn_id": "u2",
        },
        ctx=ctx,
    )
    assert out  # produces at least one event
    assert ctx.exec_call_id == "planner-u2-exec"
    assert ctx.turn_id == "u2"
    assert ctx._seen_engine_ops == set()  # reset on new turn


def test_client_response_emits_distinct_kind() -> None:
    transform = NativeObservabilityTransform()
    ctx = TransformContext(agent_id="planner", run_id="run-1")
    out = transform.transform(
        {
            "_source": "boundary",
            "kind": ObsEventKind.CLIENT_RESPONSE.value,
            "correlation_id": 1,
            "payload": {"finish_reason": "stop"},
        },
        ctx=ctx,
    )
    assert len(out) == 1
    assert out[0]["kind"] == "client_response"
    assert out[0]["finish_reason"] == "stop"


def test_cross_turn_dedup_not_suppressed_after_new_user_turn() -> None:
    transform = NativeObservabilityTransform()
    ctx = TransformContext(agent_id="planner", run_id="run-1")
    boundary = {
        "_source": "boundary",
        "kind": ObsEventKind.ENGINE_IO.value,
        "correlation_id": 1,
        "payload": {"op": "LLM_CALL", "messages": []},
    }
    assert transform.transform(boundary, ctx=ctx)
    ctx._seen_engine_ops.add((1, "LLM_CALL"))
    ctx.turn_id = "t2"
    ctx._seen_engine_ops.clear()
    out = transform.transform(boundary, ctx=ctx)
    assert out and out[0]["kind"] == "llm_call_start"
