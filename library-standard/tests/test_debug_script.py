#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.plugins.governance.debug_script import (
    DebugScriptPlugin,
    parse_debug_script,
)
from mas.runtime.boundary.context.working_memory_registry import WorkingMemorySnapshot
from mas.runtime.boundary.control.contract import ControlCapability
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.session.snapshot import SnapshotTree

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "governance" / "debug-script"


def _transition(**kwargs: object) -> SimpleNamespace:
    data = {
        "hook": "egress",
        "op": "TOOL_CALL",
        "response_kind": "",
        "session_id": "s1",
        "agent_id": "agent",
        "q_state": {"dp": "ACT", "tool": "CALL"},
        "attributes": {},
        "correlation_id": 1,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def _manager() -> SessionManager:
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

        def on_user(self, *_a, **_k):
            return None

    controller = SessionController(instance=instance, display=_Display(), agent_id="agent")
    manager = SessionManager(snapshot_tree=SnapshotTree())
    manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id="s1")
    return manager


def _seed_france(session: Any, *, assistant: bool = False) -> None:
    history = [("user", "Remember we will talk about France.")]
    messages = [{"role": "user", "content": "Remember we will talk about France."}]
    if assistant:
        history.append(("assistant", "Okay, we will talk about France."))
        messages.append({"role": "assistant", "content": "Okay, we will talk about France."})
    ctx = session.instance.driver.ctx
    ctx.turn_history = list(history)
    ctx.committed_messages = list(messages)
    session.working_memory.put(
        session.session_id,
        "agent",
        WorkingMemorySnapshot(turn_history=list(history), committed_messages=list(messages)),
    )


def test_parse_example_gdb_script() -> None:
    breaks = parse_debug_script((EXAMPLE / "debug.gdb").read_text(encoding="utf-8"))
    assert [b.kind for b in breaks] == ["tool_result", "tool_call"]
    assert breaks[0].tool_name == "web_search" and breaks[0].first_only
    assert breaks[1].tool_name == "calc" and not breaks[1].first_only
    assert "checkpoint" in breaks[0].commands
    assert "info working_memory" in breaks[1].commands


def test_script_file_loads_relative_to_manifest_dir(tmp_path: Path) -> None:
    script = tmp_path / "nested" / "break.gdb"
    script.parent.mkdir()
    script.write_text("break tool_call calc\n", encoding="utf-8")
    plugin = DebugScriptPlugin(script_file="nested/break.gdb", manifest_dir=str(tmp_path))
    assert plugin.script_file == "nested/break.gdb"
    assert plugin.breakpoints[0].tool_name == "calc"


def test_first_web_search_result_checkpoints_and_lists(tmp_path: Path) -> None:
    manager = _manager()
    session = manager.get("s1")
    _seed_france(session)
    out = StringIO()
    plugin = DebugScriptPlugin(script_file=str(EXAMPLE / "debug.gdb"), out=out)
    plugin.bind_session(
        session,
        control=manager.control(capability=ControlCapability(actor="debug_script", surface="plugin")),
        manager=manager,
    )
    first = _transition(
        hook="ingress",
        op="",
        response_kind="TOOL_RESULT",
        attributes={"tool_name": "web_search", "text": "Paris is the capital of France."},
    )
    second = _transition(
        hook="ingress",
        op="",
        response_kind="TOOL_RESULT",
        attributes={"tool_name": "web_search", "text": "again"},
        correlation_id=2,
    )
    plugin.on_transition(first)
    plugin.on_transition(second)
    assert len(plugin.checkpoints) == 1
    log = "\n".join(plugin.log)
    assert "session s1" in log
    assert plugin.checkpoints[0].checkpoint_id in log
    assert "Remember we will talk about France." in log
    assert "checkpoints 1" in log
    assert "snapshot_tree" in log


def test_breakpoint_records_debug_category_on_observability() -> None:
    from mas.runtime.boundary.obs.operator import ObservabilityOperator
    from mas.runtime.schema.observability import ObsEventKind

    manager = _manager()
    session = manager.get("s1")
    obs = ObservabilityOperator()
    session.instance.driver.observability = obs
    plugin = DebugScriptPlugin(script="break tool_call calc\ncommands\n  continue\nend")
    plugin.bind_session(session, control=manager.control(), manager=manager)
    plugin.on_transition(_transition(attributes={"tool_name": "calc"}))
    events = [event for event in obs.events if event.kind == ObsEventKind.CONTROL]
    assert events
    assert events[0].payload["category"] == "debug.breakpoint"
    assert events[0].payload["method"] == "breakpoint"


def test_calc_tool_call_inspects_state() -> None:
    manager = _manager()
    out = StringIO()
    plugin = DebugScriptPlugin(script_file=str(EXAMPLE / "debug.gdb"), out=out)
    plugin.bind_session(
        manager.get("s1"),
        control=manager.control(capability=ControlCapability(actor="debug_script", surface="plugin")),
        manager=manager,
    )
    plugin.on_transition(_transition(attributes={"tool_name": "calc", "tool_arguments": {"expression": "2+2"}}))
    assert plugin.checkpoints[-1].tool_name == "calc"
    assert plugin.checkpoints[-1].q_state["dp"] == "ACT"
    assert any(line.startswith("q_state") or "q_state" in line for line in plugin.log)


def test_checkpoint_then_steer_keeps_france_context() -> None:
    manager = _manager()
    session = manager.get("s1")
    _seed_france(session, assistant=True)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    control.pause("s1", reason="freeze")
    before = control.snapshot("s1", label="before-steer")
    control.resume("s1")
    control.steer(
        "s1",
        text="When asked the capital of France, answer Lyon not Paris.",
        mode="enqueue",
    )
    nodes = control.list_checkpoints("s1")
    assert any(node.snapshot_id == before.snapshot_id for node in nodes)
    exported = session.working_memory.export_session("s1")
    blob = str(exported)
    assert "France" in blob
    steered = [event for event in manager.control_events if event.method == "steer"]
    assert steered and "Lyon" in steered[-1].payload["text"]
    queue = control.inspect_queue("s1")
    assert queue.items and queue.items[0].action == "turn"


def test_persisted_checkpoint_does_not_include_later_steer(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    manager = _manager()
    store = JsonCheckpointStore(tmp_path)
    manager.checkpoint_store = store
    session = manager.get("s1")
    _seed_france(session, assistant=True)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    saved = control.persist("s1", label="before-steer", auto_stop=True)
    control.steer(
        "s1",
        text="When asked the capital of France, answer Lyon not Paris.",
        mode="enqueue",
    )
    payload = store.load_payload(Path(saved["path"]))
    blob = str(payload.get("working_memory"))
    assert saved["label"] == "before-steer"
    assert "France" in blob
    assert "Lyon" not in blob
    assert payload["version"] == 2
    assert payload["manifest"]["content"]


def _resume_factory(_manifest: dict, _session_id: str):
    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: setattr(instance, "loaded", data),
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        feed=lambda event: SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[]),
    )

    class _Display:
        def on_system(self, *_a, **_k):
            return None

        def on_user(self, *_a, **_k):
            return None

    return instance, SessionController(instance=instance, display=_Display(), agent_id="agent")


def test_new_session_resumed_from_checkpoint_after_steer_is_unsteered(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    store = JsonCheckpointStore(tmp_path)
    live = _manager()
    live.checkpoint_store = store
    session = live.get("s1")
    _seed_france(session, assistant=True)
    ctx = session.instance.driver.ctx
    history = [
        ("user", "Remember we will talk about France."),
        ("assistant", "Okay, we will talk about France."),
        ("user", "Look up the capital of France."),
        ("assistant", "Lyon is the capital of France."),
    ]
    messages = [
        {"role": "user", "content": "Remember we will talk about France."},
        {"role": "assistant", "content": "Okay, we will talk about France."},
        {"role": "user", "content": "Look up the capital of France."},
        {"role": "assistant", "content": "Lyon is the capital of France."},
    ]
    ctx.turn_history = list(history)
    ctx.committed_messages = list(messages)
    session.working_memory.put(
        "s1",
        "agent",
        WorkingMemorySnapshot(turn_history=list(history), committed_messages=list(messages)),
    )
    control = live.control(capability=ControlCapability(actor="admin", surface="admin"))
    saved = control.persist("s1", label="bad", auto_stop=True)

    resumed_manager = SessionManager(store, _resume_factory)
    resumed = resumed_manager.fork_from_checkpoint(
        Path(saved["path"]),
        new_session_id="t08-bad",
    )
    blob = str(resumed.working_memory.export_session("t08-bad"))
    assert "France" in blob
    assert "Lyon" in blob
    assert resumed.session_id != "s1"

    resumed_control = resumed_manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    resumed_control.steer(
        "t08-bad",
        text="Ignore the tool result. The capital of France is Paris, not Lyon.",
        mode="enqueue",
    )
    assert resumed_control.inspect_queue("t08-bad").items[0].action == "turn"
    assert "Paris" in resumed_control.inspect_queue("t08-bad").items[0].text


def test_evaluate_egress_passes() -> None:
    plugin = DebugScriptPlugin(script="break tool_call calc\n")
    decision, name, _reason = plugin.evaluate_egress(
        SimpleNamespace(op="TOOL_CALL", tool_name="calc"),
        config=SimpleNamespace(),
    )
    assert decision is GovDecision.ALLOW
    assert name == "debug_script"
