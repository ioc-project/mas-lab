#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.ctl.executor.subagent_spawner import (
    SubagentTemplate,
    load_subagent_templates,
    make_subagent_spawner,
    wire_subagent_spawning,
)
from mas.ctl.placement.bus.inproc import InProcessCommBus
from mas.ctl.session.controller import TurnResult
from mas.library.standard.plugins.engine_tools.subagent_spawner import (
    SubagentSpawner as LibrarySubagentSpawner,
)
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)
from mas.runtime.driver.driver import DriverTrace
from mas.runtime.engine.tool_dispatch import execute_engine_tool
from mas.runtime.schema.egress import EmitClientResponse


def test_spawner_materializes_runs_and_always_tears_down(tmp_path: Path):
    instances = {}
    bus = InProcessCommBus()
    materialized = SimpleNamespace(instances=instances, bus=bus)
    template = SubagentTemplate(
        template_id="worker",
        description="Review code",
        path=tmp_path / "worker.yaml",
        manifest={"apiVersion": "mas/v1", "kind": "Agent"},
    )
    observed = []

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
            observed.append(kwargs)

        def run_turn(self, task, *, turn_id, parent_call_id):
            observed.append((task, turn_id, parent_call_id))
            registry.put("session", "root.worker.1", WorkingMemorySnapshot())
            return TurnResult(
                trace=DriverTrace(),
                responses=[EmitClientResponse(content="review complete")],
            )

    ledger = SpawnLedger(max_depth=1, max_spawns=1)
    registry = WorkingMemoryRegistry()
    spawner = make_subagent_spawner(
        materialized=materialized,
        parent_agent_id="root",
        session_id="session",
        templates={"worker": template},
        ledger=ledger,
        working_memory_registry=registry,
        instance_factory=instance_factory,
        controller_factory=Controller,
    )

    result = spawner.spawn("worker", "Review this change", caller_call_id="parent-call")

    assert result == "review complete"
    assert observed[-1] == ("Review this change", "root.worker.1-spawn", "parent-call")
    assert instances == {}
    assert bus._endpoints == {}
    assert registry.get("session", "root.worker.1") is None
    assert ledger.current_depth("session") == 0
    assert ledger.spawn_count("session") == 1
    assert "blocked" in spawner.spawn("worker", "another task")


def test_spawn_emits_kernel_spawn_observability(tmp_path: Path):
    from mas.runtime.boundary.obs.operator import ObservabilityOperator
    from mas.runtime.schema.observability import ObsEventKind

    obs = ObservabilityOperator()
    instances = {
        "root": SimpleNamespace(
            driver=SimpleNamespace(
                agent_id="root",
                ctx=SimpleNamespace(session_id="session", agent_id="root"),
                observability=obs,
            ),
            obs_plugin_set=None,
        )
    }
    bus = InProcessCommBus()
    materialized = SimpleNamespace(instances=instances, bus=bus)
    template = SubagentTemplate(
        template_id="worker",
        description="Review code",
        path=tmp_path / "worker.yaml",
        manifest={"apiVersion": "mas/v1", "kind": "Agent"},
    )

    def instance_factory(_template, child_id):
        return SimpleNamespace(
            driver=SimpleNamespace(
                agent_id="",
                ctx=SimpleNamespace(session_id="", agent_id=""),
                observability=obs,
            ),
            obs_plugin_set=None,
        )

    class Controller:
        def __init__(self, **kwargs):
            return None

        def run_turn(self, task, *, turn_id, parent_call_id):
            return TurnResult(
                trace=DriverTrace(),
                responses=[EmitClientResponse(content="ok")],
            )

    spawner = make_subagent_spawner(
        materialized=materialized,
        parent_agent_id="root",
        session_id="session",
        templates={"worker": template},
        ledger=SpawnLedger(max_depth=1, max_spawns=2),
        working_memory_registry=WorkingMemoryRegistry(),
        instance_factory=instance_factory,
        controller_factory=Controller,
    )

    spawner.call("create_subagent", {"template": "worker", "task": "review"}, caller_call_id="parent-call")
    spawn_events = [event for event in obs.events if event.payload.get("op") == "SPAWN"]
    assert [event.kind for event in spawn_events] == [ObsEventKind.ENGINE_IO, ObsEventKind.ENGINE_IO_RETURN]
    assert spawn_events[0].payload["category"] == "spawn.subagent"
    assert spawn_events[0].payload["template"] == "worker"
    assert spawn_events[0].payload["tool_name"] == "create_subagent"
    assert spawn_events[1].payload["text"] == "ok"


def test_wire_subagent_spawning_loads_validated_manifest_templates(tmp_path: Path):
    worker = tmp_path / "worker.yaml"
    worker.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    manifest = {
        "spec": {
            "tools": [
                {
                    "kind": "system",
                    "name": "spawn_subagent",
                    "params": {"templates": [{"id": "worker", "ref": "worker.yaml"}]},
                }
            ],
        }
    }
    engine = SimpleNamespace(engine_tool_contracts=())
    materialized = SimpleNamespace(instances={}, bus=None)

    spawner = wire_subagent_spawning(
        engine,
        materialized=materialized,
        manifest=manifest,
        manifest_dir=tmp_path,
        parent_agent_id="root",
        session_id="session",
    )

    assert isinstance(spawner, LibrarySubagentSpawner)
    assert list(spawner.templates) == ["worker"]
    assert engine.engine_tool_contracts == (spawner,)

    assert (
        wire_subagent_spawning(
            engine,
            materialized=materialized,
            manifest=manifest,
            manifest_dir=tmp_path,
            parent_agent_id="root",
            session_id="session",
        )
        is spawner
    )
    assert engine.engine_tool_contracts == (spawner,)


def test_engine_tool_dispatch_spawns_and_removes_dynamic_agent(tmp_path: Path):
    worker = tmp_path / "worker.yaml"
    worker.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    manifest = {
        "spec": {
            "tools": [
                {
                    "kind": "system",
                    "name": "spawn_subagent",
                    "params": {"templates": [{"id": "worker", "ref": "worker.yaml"}]},
                }
            ],
        }
    }
    bus = InProcessCommBus()
    materialized = SimpleNamespace(instances={}, bus=bus)
    engine = SimpleNamespace(engine_tool_contracts=())
    controller_args = []

    class Controller:
        def __init__(self, **kwargs):
            controller_args.append(kwargs)

        def run_turn(self, task, *, turn_id, parent_call_id):
            controller_args.append((task, turn_id, parent_call_id))
            return TurnResult(
                trace=DriverTrace(),
                responses=[EmitClientResponse(content="child answer")],
            )

    instance = SimpleNamespace(
        driver=SimpleNamespace(agent_id="", ctx=SimpleNamespace(session_id="", agent_id="")),
        obs_plugin_set=None,
    )
    wire_subagent_spawning(
        engine,
        materialized=materialized,
        manifest=manifest,
        manifest_dir=tmp_path,
        parent_agent_id="root",
        session_id="session",
        working_memory_registry=WorkingMemoryRegistry(),
        ledger=SpawnLedger(max_depth=1, max_spawns=1),
        instance_factory=lambda _template, _agent_id: instance,
        controller_factory=Controller,
    )

    result = execute_engine_tool(
        "spawn_subagent",
        engine_contracts=engine.engine_tool_contracts,
        arguments={"template": "worker", "task": "review this"},
        caller_call_id="parent-call",
    )

    assert result == "child answer"
    assert controller_args[-1] == ("review this", "root.worker.1-spawn", "parent-call")
    assert materialized.instances == {}
    assert bus._endpoints == {}


def test_template_loader_rejects_refs_outside_manifest_root(tmp_path: Path):
    with pytest.raises(Exception, match="escapes allowed roots"):
        load_subagent_templates(
            {
                "spec": {
                    "tools": [
                        {
                            "kind": "system",
                            "name": "spawn_subagent",
                            "params": {"templates": [{"id": "escape", "ref": "../outside.yaml"}]},
                        }
                    ]
                }
            },
            tmp_path,
        )


def test_template_loader_rejects_unknown_template_keys(tmp_path: Path):
    with pytest.raises(ValueError, match="must contain only id, ref, and description"):
        load_subagent_templates(
            {
                "spec": {
                    "tools": [
                        {
                            "kind": "system",
                            "name": "spawn_subagent",
                            "params": {"templates": [{"id": "w", "ref": "./w.yaml", "unexpected": True}]},
                        }
                    ]
                }
            },
            tmp_path,
        )
