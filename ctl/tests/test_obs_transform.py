#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Observability transform chain tests — uses runtime loader, not ctl pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.library.standard.lib.observability.native.transform import BoundaryPassthroughTransform, TransformContext
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.plugins import ObsPluginSet, build_observability_plugins


@dataclass
class _FakeDriver:
    observability: ObservabilityOperator = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)


def test_boundary_passthrough_forwards_session_records() -> None:
    transform = BoundaryPassthroughTransform()
    ctx = TransformContext(agent_id="planner")
    session = {"_source": "session", "session_kind": "turn_start", "turn_id": "t1"}
    assert transform.transform(session, ctx=ctx) == [session]


def test_plugin_set_subscribes_and_records_via_operator(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="planner")
    plugin_set = ObsPluginSet(plugins=plugins)
    op = ObservabilityOperator()
    plugin_set.subscribe_to(op, agent_id="planner")
    assert len(op._subscribers) == 1

    op.record_session("user_input", text="hello", call_id="t1-exec", turn_id="u1")
    op.drain_plugin_queue()  # subscribe_to enables async; drain before checking file

    events_path = tmp_path / "events.jsonl"
    assert events_path.stat().st_size > 0
