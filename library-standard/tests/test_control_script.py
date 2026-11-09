#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.plugins.control.script import (
    parse_control_chain,
    parse_control_script,
    resolve_curl_data,
)
from mas.library.standard.plugins.governance.debug_script import DebugScriptPlugin
from mas.runtime.boundary.control.contract import ControlCapability, SessionNotStopped
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.schema.observability import ObsEventKind
from mas.runtime.session.snapshot import SnapshotTree

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "governance" / "debug-script"


def _manager(*, obs: ObservabilityOperator | None = None) -> SessionManager:
    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx, observability=obs),
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


def test_chain_and_script_file_parse_the_same(tmp_path: Path) -> None:
    body = (EXAMPLE / "recover.ctl").read_text(encoding="utf-8")
    from_file = parse_control_script(body)
    from_chain = parse_control_chain(
        [
            "pause",
            "--reason",
            "bad-session",
            "persist",
            "--label",
            "bad",
            "--auto-stop",
            "inspect",
            "checkpoints",
        ]
    )
    assert [item.verb for item in from_file] == [item.verb for item in from_chain]
    assert from_file[0].kwargs["reason"] == "bad-session"
    assert from_file[1].kwargs["label"] == "bad"
    assert from_file[1].kwargs["auto_stop"] is True
    copied = tmp_path / "recover.ctl"
    copied.write_text(body, encoding="utf-8")
    assert parse_control_script(resolve_curl_data(f"@{copied}")) == from_file
    assert parse_control_script(resolve_curl_data(body))[0].verb == "pause"


def test_persist_requires_stopped_session_or_auto_stop(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    manager = _manager()
    manager.checkpoint_store = JsonCheckpointStore(tmp_path)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    with pytest.raises(SessionNotStopped):
        control.persist("s1", label="bad")
    saved = control.persist("s1", label="bad", auto_stop=True)
    assert Path(saved["path"]).is_file()
    assert manager.get("s1").status.value == "paused"


def test_run_script_executes_recover_file(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    manager = _manager()
    manager.checkpoint_store = JsonCheckpointStore(tmp_path)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    results = control.run_script("s1", script_file=str(EXAMPLE / "recover.ctl"))
    assert results
    assert manager.get("s1").status.value == "paused"
    methods = [event.method for event in manager.control_events]
    assert "pause" in methods
    assert "persist" in methods
    assert "inspect" in methods
    assert "list_checkpoints" in methods
    assert "run_script" in methods


def test_control_verbs_reach_native_observability_contract() -> None:
    op = ObservabilityOperator()
    manager = _manager(obs=op)
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    control.pause("s1", reason="bad-session")
    control.inspect("s1")
    kinds = [event.kind for event in op.events]
    assert ObsEventKind.CONTROL in kinds
    payload_methods = {event.payload.get("method") for event in op.events if event.kind == ObsEventKind.CONTROL}
    assert "pause" in payload_methods
    assert "inspect" in payload_methods
    assert all(
        event.payload.get("category", "").startswith("control.")
        for event in op.events
        if event.kind == ObsEventKind.CONTROL
    )


def test_debug_script_at_file_is_the_same_as_script_file() -> None:
    plugin = DebugScriptPlugin(script=f"@{(EXAMPLE / 'debug.gdb')}")
    other = DebugScriptPlugin(script_file=str(EXAMPLE / "debug.gdb"))
    assert [b.kind for b in plugin.breakpoints] == [b.kind for b in other.breakpoints]
    assert plugin.breakpoints[0].tool_name == "web_search"


def test_backtrack_records_a_sibling_on_the_snapshot_tree(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore
    from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
    from mas.runtime.session import ManifestRef, Session, SessionLineage

    store = JsonCheckpointStore(tmp_path)
    tree = SnapshotTree()
    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        driver=SimpleNamespace(ctx=ctx),
    )
    session = Session(
        session_id="s1",
        instance=instance,
        controller=SimpleNamespace(
            _turn=1, agent_id="agent", restore_turn=lambda n: setattr(session.controller, "_turn", n)
        ),
        working_memory=WorkingMemoryRegistry(),
        lineage=SessionLineage(session_id="s1"),
        manifest_ref=ManifestRef.from_content({"name": "agent"}),
        snapshot_tree=tree,
    )
    first = session.checkpoint(store, label="turn-1")
    session.controller._turn = 2
    session.checkpoint(store, label="turn-2")
    session.backtrack(store, steps=2, automatic=True)
    nodes = tree.list_nodes("s1")
    labels = [node.label for node in nodes]
    assert "turn-1" in labels
    assert "after-backtrack" in labels
    after = next(node for node in nodes if node.label == "after-backtrack")
    parents = {node.snapshot_id: node.parent_snapshot_id for node in nodes}
    children = [node for node in nodes if node.parent_snapshot_id == after.parent_snapshot_id]
    assert len(children) >= 2
    assert Path(first).is_file()
    assert parents[after.snapshot_id] is not None


def test_native_export_can_disable_control_category() -> None:
    from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin

    plugin = NativeObservabilityPlugin(categories_exclude=("control",))
    assert plugin._category_allowed({"kind": "control", "category": "control.pause"}) is False
    assert plugin._category_allowed({"kind": "tool_call_start"}) is True
    only_control = NativeObservabilityPlugin(categories_include=("control",))
    assert only_control._category_allowed({"kind": "control", "category": "control.steer"}) is True
    assert only_control._category_allowed({"kind": "llm_call_start"}) is False


def test_steer_enqueue_at_head_from_script() -> None:
    from mas.library.standard.plugins.control.script import run_control_script

    manager = _manager()
    control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))
    control.enqueue_input("s1", text="later", source="peer")
    run_control_script(
        control,
        "s1",
        parse_control_script("steer --text now --enqueue --at head"),
    )
    texts = [item.text for item in control.inspect_queue("s1").items]
    assert texts[0] == "now"
    assert texts[1] == "later"
    run_control_script(
        control,
        "s1",
        parse_control_script("enqueue --text mid --at 1"),
    )
    texts = [item.text for item in control.inspect_queue("s1").items]
    assert texts == ["now", "mid", "later"]
    chained = parse_control_chain(["steer", "--text", "x", "--enqueue", "enqueue", "--text", "y", "--at", "head"])
    assert [item.verb for item in chained] == ["steer", "enqueue"]
    assert chained[0].kwargs["enqueue"] is True
    assert chained[1].kwargs["at"] == "head"
