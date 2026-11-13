#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

import pytest
from mas.ctl.executor.engine_tool_context import MaterializedEngineToolContext
from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    WorkingMemorySnapshot,
)
from mas.runtime.boundary.engine_tools import EngineToolBudgetExceeded


def _instance():
    return SimpleNamespace(
        driver=SimpleNamespace(agent_id="", ctx=SimpleNamespace(session_id="", agent_id="")),
        obs_plugin_set=None,
    )


def _context(**overrides):
    defaults = dict(
        materialized=SimpleNamespace(instances={}, bus=None),
        session_id="session",
        parent_agent_id="root",
        ledger=SpawnLedger(max_depth=1, max_spawns=1),
        working_memory_registry=WorkingMemoryRegistry(),
        instance_factory=lambda _m, _d, _cid, _tid: _instance(),
    )
    defaults.update(overrides)
    return MaterializedEngineToolContext(**defaults)


def test_spawn_instance_raises_rather_than_silently_refusing():
    ctx = _context(ledger=SpawnLedger(max_depth=1, max_spawns=1))

    first = ctx.spawn_instance({}, template_id="worker")
    assert first == "root.worker.1"

    # Budget is spent: a plugin cannot get a second child by asking again.
    with pytest.raises(EngineToolBudgetExceeded):
        ctx.spawn_instance({}, template_id="worker")


def test_teardown_releases_instance_memory_and_depth():
    registry = WorkingMemoryRegistry()
    materialized = SimpleNamespace(instances={}, bus=None)
    ctx = _context(materialized=materialized, working_memory_registry=registry)

    child_id = ctx.spawn_instance({}, template_id="worker")
    registry.put("session", child_id, WorkingMemorySnapshot())
    assert materialized.instances[child_id] is not None
    assert ctx.ledger.agent_depth("session", child_id) == 1

    ctx.teardown_instance(child_id)

    assert child_id not in materialized.instances
    assert registry.get("session", child_id) is None
    assert ctx.ledger.current_depth("session") == 0


def test_engine_tool_provider_category_resolves_the_spawner():
    from mas.library.standard.plugins.engine_tools.subagent_spawner import SubagentSpawner
    from mas.runtime.registry import get_registry

    variant = get_registry().resolve_by_type("engine_tool_provider", "spawn_subagent")

    assert variant is not None
    assert variant.load_class() is SubagentSpawner


def test_spawned_child_gets_flavour_spec(monkeypatch):
    captured: dict = {}

    def fake_instantiate(options, hitl=None):
        captured["flavour"] = options.flavour_spec
        captured["entry"] = options.entry_id
        return _instance(), None

    monkeypatch.setattr("mas.ctl.session.bootstrap.instantiate_runtime", fake_instantiate)
    flavour = {"control": [{"ref": "gov.yaml"}]}
    ctx = _context(instance_factory=None, flavour_spec=flavour, entry_id="desk")
    ctx.materialized.instances["root"] = _instance()
    ctx.spawn_instance({}, template_id="worker")
    assert captured["flavour"] is flavour
    assert captured["entry"] == "desk"


def _blocked_turn(reason: str):
    return SimpleNamespace(
        responses=[],
        text="",
        trace=SimpleNamespace(
            client_responses=[],
            boundary_errors=[SimpleNamespace(code="GOV_BLOCK", message=reason)],
        ),
    )


class _FakeController:
    def __init__(self, result, **_kw):
        self._result = result

    def run_turn(self, *_a, **_kw):
        return self._result


def test_run_turn_raises_with_the_governance_reason():
    """A withheld hand-off tells the caller why, not just that the turn failed."""
    from mas.ctl.executor.engine_tool_context import EngineToolTurnFailed

    ctx = _context()
    ctx.materialized.instances["worker"] = _instance()
    ctx.controller_factory = lambda **kw: _FakeController(_blocked_turn("PII in reply"), **kw)
    with pytest.raises(EngineToolTurnFailed, match="agent 'worker' turn failed: PII in reply"):
        ctx.run_turn("worker", "do it")


@pytest.mark.asyncio
async def test_arun_turn_raises_with_the_governance_reason():
    from mas.ctl.executor.engine_tool_context import EngineToolTurnFailed

    ctx = _context()
    ctx.materialized.instances["worker"] = _instance()
    ctx.controller_factory = lambda **kw: _FakeController(_blocked_turn("PII in reply"), **kw)
    with pytest.raises(EngineToolTurnFailed, match="PII in reply"):
        await ctx.arun_turn("worker", "do it")
