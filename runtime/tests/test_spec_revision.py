#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

import pytest
from mas.ctl.executor.engine_tool_context import MaterializedEngineToolContext
from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.ctl.session.control import SessionControl
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.plugins.system_tools.control import ControlTools
from mas.library.standard.plugins.system_tools.spawn_subagent import SpawnSubagentTool
from mas.runtime.boundary.control.contract import ControlCapability
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names, is_spawn_subagent_enabled
from mas.runtime.session.spec_revision import SpecDelta, SpecRevisionLog


def _manager_with_session(manifest: dict | None = None) -> tuple[SessionManager, str]:
    manager = SessionManager()
    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        feed=lambda event: SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[]),
    )

    class _Display:
        def on_system(self, *_a, **_k):
            return None

    controller = SessionController(instance=instance, display=_Display())
    doc = manifest or {
        "name": "agent",
        "spec": {"tools": [{"name": "lookup", "enabled": True}]},
    }
    manager.create(instance, controller, doc, session_id="s1")
    return manager, "s1"


def test_unknown_spec_delta_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown spec delta"):
        SpecDelta("invented", {})


def test_extra_spec_delta_keys_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown spec delta keys"):
        SpecDelta.from_mapping({"kind": "disable_tool", "payload": {"name": "x"}, "sneak": True})


def test_disable_tool_is_a_traced_revision_and_gates_advertise() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    ctx = session.instance.driver.ctx
    assert is_spawn_subagent_enabled(manager.spec_log.current_spec(sid)) is False
    before = manager.spec_log.latest(sid)
    assert before is not None and before.revision == 0
    assert "lookup" not in disabled_tool_names(current_spec_for_advertise(ctx))
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    rev = control.disable_tool(sid, name="lookup", reason="governance")
    assert rev == 1
    spec = manager.spec_log.current_spec(sid)
    assert spec["tools"][0]["enabled"] is False
    assert "lookup" in disabled_tool_names(current_spec_for_advertise(ctx))
    events = [e for e in manager.control_events if e.kind == "spec_revised"]
    assert events and events[0].payload["revision"] == 1
    snap = session.take_snapshot(label="after-disable")
    assert snap.spec_revision == 1


def test_restore_snapshot_restores_spec_revision() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    origin = session.take_snapshot(label="r0")
    manager.control().disable_tool(sid, name="lookup", reason="temp")
    assert session.spec_revision == 1
    session.restore_snapshot(origin)
    assert session.spec_revision == 0
    assert session.manifest_ref is not None
    tools = (session.manifest_ref.content.get("spec") or {}).get("tools") or []
    assert tools[0].get("enabled") is not False
    assert manager.spec_log.latest(sid) is not None
    assert manager.spec_log.latest(sid).revision == 0


def test_three_surfaces_same_event_kinds() -> None:
    manager, sid = _manager_with_session()
    session = manager.get(sid)
    origin = session.take_snapshot(label="n0")
    session.take_snapshot(label="n1")
    for surface in ("plugin", "llm", "admin"):
        cap = ControlCapability(actor=surface, surface=surface)
        control = SessionControl(manager, capability=cap)
        control.pause(sid, reason=surface)
        control.resume(sid)
        control.inspect(sid)
        control.list_checkpoints(sid)
        control.navigate(sid, to=origin.ref.snapshot_id, reason="walk")
        control.disable_tool(sid, name="lookup", reason=surface)
        if surface == "llm":
            tools = ControlTools(control, sid)
            ctx = SimpleNamespace(allow_control_tools=True)
            names = [t["name"] for t in tools.on_collect_tools(ctx=ctx)]
            assert "pause_session" in names
            assert "list_checkpoints" in names
            assert "navigate_checkpoint" in names
            assert "inspect_session" in names
            assert "cancel_inflight" in names
            tools.on_execute_tool("list_checkpoints", {})
            tools.on_execute_tool("inspect_session", {})
            tools.on_execute_tool(
                "navigate_checkpoint",
                {"to": origin.ref.snapshot_id, "reason": "llm-walk"},
            )
    inspects = [e for e in manager.control_events if e.kind == "checkpoint_inspected"]
    navigates = [e for e in manager.control_events if e.kind == "checkpoint_navigated"]
    pauses = [e for e in manager.control_events if e.kind == "session_paused"]
    disables = [e for e in manager.control_events if e.kind == "spec_revised"]
    assert {e.surface for e in inspects} == {"plugin", "llm", "admin"}
    assert {e.surface for e in navigates} == {"plugin", "llm", "admin"}
    assert {e.surface for e in pauses} == {"plugin", "llm", "admin"}
    assert {e.surface for e in disables} == {"plugin", "llm", "admin"}
    assert {e.kind for e in inspects} == {"checkpoint_inspected"}
    assert {e.kind for e in navigates} == {"checkpoint_navigated"}
    assert {e.kind for e in pauses} == {"session_paused"}
    assert {e.kind for e in disables} == {"spec_revised"}


def test_spawn_llm_tool_is_advertisement_not_a_second_implementation() -> None:
    tool = SpawnSubagentTool()
    assert tool.on_execute_tool("create_subagent", {"template": "worker", "task": "x"}) == (
        "[spawn_subagent] unavailable: orchestration contract is not wired"
    )


def test_spawn_copies_parent_spec_revision() -> None:
    log = SpecRevisionLog()
    log.materialize("session", {"spec": {"tools": []}})
    log.apply("session", SpecDelta("set_pattern", {"pattern": "react"}), actor="admin", reason="evolve")
    parent = SimpleNamespace(
        driver=SimpleNamespace(agent_id="root", ctx=SimpleNamespace(session_id="session", spec_log=log)),
        obs_plugin_set=None,
    )
    materialized = SimpleNamespace(instances={"root": parent}, bus=None)

    def child_factory(_m, _d, _cid, _tid):
        return SimpleNamespace(
            driver=SimpleNamespace(agent_id="", ctx=SimpleNamespace(session_id="", agent_id="")),
            obs_plugin_set=None,
        )

    ctx = MaterializedEngineToolContext(
        materialized=materialized,
        session_id="session",
        parent_agent_id="root",
        ledger=SpawnLedger(max_depth=1, max_spawns=1),
        instance_factory=child_factory,
    )
    child_id = ctx.spawn_instance({}, template_id="worker")
    child = materialized.instances[child_id]
    assert child.spec_revision == 1
    assert child.driver.ctx.spec_revision == 1
    assert child.driver.ctx.current_spec["design_pattern"] == "react"


def test_plan_mode_is_a_spec_revision_and_restore_rolls_it_back() -> None:
    from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names
    from mas.runtime.harness.recipes import apply_default_mode, apply_plan_mode

    manifest = {
        "name": "agent",
        "spec": {
            "tools": [
                {"kind": "function", "name": "lookup", "enabled": True},
                {"kind": "function", "name": "write", "enabled": True},
            ]
        },
    }
    manager, sid = _manager_with_session(manifest)
    session = manager.get(sid)
    origin = session.take_snapshot(label="before-plan")
    control = manager.control()
    apply_plan_mode(control, sid, write_tools=("write",))
    assert session.spec_revision >= 1
    ctx = session.instance.driver.ctx
    assert "write" in disabled_tool_names(current_spec_for_advertise(ctx))
    events = [e for e in manager.control_events if e.kind in {"spec_revised", "session_paused"}]
    assert {e.kind for e in events} >= {"spec_revised", "session_paused"}
    session.restore_snapshot(origin)
    apply_default_mode(control, sid)
    assert session.spec_revision == 0
    assert "write" not in disabled_tool_names(current_spec_for_advertise(ctx))
