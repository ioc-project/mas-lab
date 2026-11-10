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
            "system_plugins": [{"subagents": {"templates": [{"id": "worker", "ref": "worker.yaml"}]}}],
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


def test_wire_uses_the_subagents_plugin_ledger(tmp_path: Path):
    from mas.library.standard.plugins.system_tools.spawn_subagent import SubagentsPlugin
    from mas.runtime.system_plugins import BoundSystemPlugins, SessionContext
    from mas.runtime.system_plugins.load import _Bound

    worker = tmp_path / "worker.yaml"
    worker.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    plugin = SubagentsPlugin(max_spawns=1, max_depth=1, templates=[{"id": "worker", "ref": "worker.yaml"}])
    bound = BoundSystemPlugins()
    bound.add(
        _Bound(
            name="subagents",
            urn="mas.system_plugin.subagents",
            plugin=plugin,
            state=plugin.bind(SessionContext()),
            settings={"max_spawns": 1, "max_depth": 1},
            capability="orchestration",
        )
    )
    bound.state_for_capability("orchestration").ledger.enter(
        "session", child_agent_id="root.w.1", parent_agent_id="root"
    )
    engine = SimpleNamespace(
        engine_tool_contracts=(),
        tool_provider=SimpleNamespace(system_plugins=lambda: bound),
    )
    materialized = SimpleNamespace(instances={}, bus=None)
    spawner = wire_subagent_spawning(
        engine,
        materialized=materialized,
        manifest={
            "spec": {
                "system_plugins": [{"subagents": {"templates": [{"id": "worker", "ref": "worker.yaml"}]}}],
            }
        },
        manifest_dir=tmp_path,
        parent_agent_id="root",
        session_id="session",
    )
    ledger = bound.state_for_capability("orchestration").ledger
    assert spawner.context.ledger is ledger
    assert materialized._subagent_spawn_ledger is ledger
    assert not ledger.allow_spawn("session", "root")


def test_engine_tool_dispatch_spawns_and_removes_dynamic_agent(tmp_path: Path):
    worker = tmp_path / "worker.yaml"
    worker.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    manifest = {
        "spec": {
            "system_plugins": [{"subagents": {"templates": [{"id": "worker", "ref": "worker.yaml"}]}}],
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
        "create_subagent",
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
            {"spec": {"system_plugins": [{"subagents": {"templates": [{"id": "escape", "ref": "../outside.yaml"}]}}]}},
            tmp_path,
        )


def test_template_loader_rejects_unknown_template_keys(tmp_path: Path):
    with pytest.raises(ValueError, match="must contain only id, ref, and description"):
        load_subagent_templates(
            {
                "spec": {
                    "system_plugins": [
                        {"subagents": {"templates": [{"id": "w", "ref": "./w.yaml", "unexpected": True}]}}
                    ]
                }
            },
            tmp_path,
        )


@pytest.mark.parametrize(
    "legacy_name",
    ["spawn_subagent", "create_subagent"],
)
def test_deprecated_kind_system_spelling_grants_the_same_spawn_params(legacy_name: str):
    from mas.runtime.engine.tools import is_spawn_subagent_enabled, spawn_subagent_params

    params = {"templates": [{"id": "w", "ref": "w.yaml"}], "max_spawns": 2}
    legacy = {"tools": [{"kind": "system", "name": legacy_name, "params": params}]}
    modern = {"system_plugins": [{"subagents": params}]}
    assert spawn_subagent_params(legacy) == spawn_subagent_params(modern) == params
    off = {"tools": [{"kind": "system", "name": legacy_name, "enabled": False}]}
    assert spawn_subagent_params(off) is None
    assert not is_spawn_subagent_enabled(off)


def _plugin_engine(tmp_path: Path, *, attach_first: bool):
    """A live engine whose manifest grants the subagents plugin, wired in either order."""
    from mas.runtime.engine.leaf import leaf_engine
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools

    (tmp_path / "worker.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    spec = {"system_plugins": [{"subagents": {"templates": [{"id": "worker", "ref": "worker.yaml"}]}}]}
    manifest = {"metadata": {"name": "root"}, "spec": spec}
    engine = LiveLlmEngine(manifest=None, use_tool_loop=True, use_cache=False)
    bus = InProcessCommBus()
    materialized = SimpleNamespace(instances={}, bus=bus)
    turns: list[tuple[str, str]] = []

    class Controller:
        def __init__(self, **_kwargs):
            pass

        def run_turn(self, task, *, turn_id, parent_call_id):
            turns.append((task, parent_call_id))
            return TurnResult(trace=DriverTrace(), responses=[EmitClientResponse(content="child answer")])

    instance = SimpleNamespace(
        driver=SimpleNamespace(agent_id="", ctx=SimpleNamespace(session_id="", agent_id="")),
        obs_plugin_set=None,
    )

    def attach() -> None:
        attach_manifest_tools(engine, manifest, tmp_path)

    def wire() -> None:
        wire_subagent_spawning(
            engine,
            materialized=materialized,
            manifest=manifest,
            manifest_dir=tmp_path,
            parent_agent_id="root",
            session_id="session",
            working_memory_registry=WorkingMemoryRegistry(),
            instance_factory=lambda _template, _agent_id: instance,
            controller_factory=Controller,
        )

    for step in (attach, wire) if attach_first else (wire, attach):
        step()
    return leaf_engine(engine), turns


@pytest.mark.parametrize("attach_first", [True, False])
def test_plugin_runs_the_child_whichever_is_wired_first(tmp_path: Path, attach_first: bool):
    leaf, turns = _plugin_engine(tmp_path, attach_first=attach_first)
    result = execute_engine_tool(
        "create_subagent",
        engine_contracts=leaf.engine_tool_contracts,
        arguments={"template": "worker", "task": "review this"},
        caller_call_id="parent-call",
    )
    assert result == "child answer"
    assert turns == [("review this", "parent-call")]
    # one owner: the plugin; no second contract competes for the tool
    assert [c for c in leaf.engine_tool_contracts if getattr(c, "is_subagent_tool", None)] == []


@pytest.mark.parametrize("attach_first", [True, False])
def test_plugin_runs_the_child_on_the_async_path(tmp_path: Path, attach_first: bool):
    import asyncio

    from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

    leaf, turns = _plugin_engine(tmp_path, attach_first=attach_first)
    spawner = leaf.tool_provider.system_plugins().state_for_capability("orchestration").spawner

    def _sync_spawn(*_a, **_k):
        raise AssertionError("the async path ran the blocking spawn")

    spawner.spawn = _sync_spawn
    result = asyncio.run(
        aexecute_engine_tool(
            "create_subagent",
            engine_contracts=leaf.engine_tool_contracts,
            arguments={"template": "worker", "task": "async task"},
            caller_call_id="pc",
        )
    )
    assert result == "child answer"
    assert turns[-1][0] == "async task"


def test_restore_keeps_the_ledger_the_spawner_holds(tmp_path: Path):
    leaf, _turns = _plugin_engine(tmp_path, attach_first=True)
    bound = leaf.tool_provider.system_plugins()
    state = bound.state_for_capability("orchestration")
    ledger = state.ledger
    assert state.spawner.context.ledger is ledger
    saved = bound.snapshot()
    execute_engine_tool(
        "create_subagent",
        engine_contracts=leaf.engine_tool_contracts,
        arguments={"template": "worker", "task": "t"},
    )
    assert ledger.spawn_count("session") == 1
    bound.restore(saved)
    assert state.ledger is ledger and state.spawner.context.ledger is ledger
    assert ledger.spawn_count("session") == 0  # spend rolled back on the object the spawner uses
