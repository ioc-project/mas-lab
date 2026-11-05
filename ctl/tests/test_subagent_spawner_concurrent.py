#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Concurrent spawn_subagent twins isolate working memory and always tear down."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.ctl.executor.subagent_spawner import SubagentTemplate, make_subagent_spawner
from mas.ctl.placement.bus.inproc import InProcessCommBus
from mas.ctl.session.controller import TurnResult
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)
from mas.runtime.driver.driver import DriverTrace
from mas.runtime.schema.egress import EmitClientResponse


def test_aspawn_two_siblings_via_gather_no_crosstalk(tmp_path: Path):
    instances = {}
    bus = InProcessCommBus()
    materialized = SimpleNamespace(instances=instances, bus=bus)
    worker = SubagentTemplate(
        template_id="worker",
        description="Review",
        path=tmp_path / "worker.yaml",
        manifest={"apiVersion": "mas/v1", "kind": "Agent"},
    )
    reviewer = SubagentTemplate(
        template_id="reviewer",
        description="Audit",
        path=tmp_path / "reviewer.yaml",
        manifest={"apiVersion": "mas/v1", "kind": "Agent"},
    )
    seen: dict[str, str] = {}

    def instance_factory(_template, child_id):
        return SimpleNamespace(
            driver=SimpleNamespace(
                agent_id="",
                ctx=SimpleNamespace(session_id="", agent_id=""),
            ),
            obs_plugin_set=None,
        )

    class Controller:
        def __init__(self, **kwargs):
            self.agent_id = kwargs["agent_id"]

        def run_turn(self, task, *, turn_id, parent_call_id):
            raise AssertionError("sync run_turn must not run on aspawn")

        async def arun_turn(self, task, *, turn_id, parent_call_id):
            registry.put(
                session_id="session",
                agent_id=self.agent_id,
                snapshot=WorkingMemorySnapshot(turn_history=[(self.agent_id, task)]),
            )
            await asyncio.sleep(0.02)
            snap = registry.get("session", self.agent_id)
            assert snap is not None
            seen[self.agent_id] = snap.turn_history[0][1]
            return TurnResult(
                trace=DriverTrace(),
                responses=[EmitClientResponse(content=f"done-{self.agent_id}")],
            )

    ledger = SpawnLedger(max_depth=1, max_spawns=4)
    registry = WorkingMemoryRegistry()
    spawner = make_subagent_spawner(
        materialized=materialized,
        parent_agent_id="root",
        session_id="session",
        templates={"worker": worker, "reviewer": reviewer},
        ledger=ledger,
        working_memory_registry=registry,
        instance_factory=instance_factory,
        controller_factory=Controller,
    )

    async def _run():
        return await asyncio.gather(
            spawner.aspawn("worker", "review code"),
            spawner.aspawn("reviewer", "audit tests"),
        )

    first, second = asyncio.run(_run())
    assert first.startswith("done-")
    assert second.startswith("done-")
    assert seen["root.worker.1"] == "review code"
    assert seen["root.reviewer.1"] == "audit tests"
    assert instances == {}
    assert ledger.current_depth("session") == 0
    assert ledger.spawn_count("session") == 2
