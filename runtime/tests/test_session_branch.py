#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.types import LifecycleState
from mas.runtime.session import ManifestRef, Session, SessionLineage
from mas.runtime.session.snapshot import SnapshotTree, persist


def test_kernel_snapshot_is_a_copy_not_an_alias() -> None:
    kernel = RuntimeKernel()
    snap = kernel.snapshot()
    kernel.q.ctrl = LifecycleState.STOPPED
    kernel.q.cot_pass = 9
    assert snap["q"]["ctrl"] == LifecycleState.RUNNING.value
    assert snap["q"]["cot_pass"] == 0


def test_branch_discard_restores_origin_and_promote_keeps_divergence() -> None:
    ctx = AutoCtxAssembler()
    ctx.note_user_input("hello")
    registry = WorkingMemoryRegistry()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {"n": instance.n}, "run": {}},
        load_checkpoint=lambda data: setattr(instance, "n", data["q"]["n"]),
        driver=SimpleNamespace(ctx=ctx),
    )
    instance.n = 1
    controller = SimpleNamespace(_turn=1, agent_id="agent", restore_turn=lambda n: setattr(controller, "_turn", n))
    session = Session(
        session_id="s",
        instance=instance,
        controller=controller,
        working_memory=registry,
        lineage=SessionLineage(session_id="s"),
        manifest_ref=ManifestRef.from_content({"name": "a"}),
        snapshot_tree=SnapshotTree(),
    )
    with session.branch() as handle:
        instance.n = 2
        handle.inspect()
    assert instance.n == 1

    with session.branch() as handle:
        instance.n = 3
        handle.promote()
    assert instance.n == 3
    live = session.snapshot_tree.live("s")
    assert live is not None


def test_persist_round_trips_spec_revision(tmp_path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    snap_tree = SnapshotTree()
    ctx = AutoCtxAssembler()
    registry = WorkingMemoryRegistry()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        load_checkpoint=lambda data: None,
        driver=SimpleNamespace(ctx=ctx),
    )
    session = Session(
        session_id="s",
        instance=instance,
        controller=SimpleNamespace(_turn=2, agent_id="agent", restore_turn=lambda n: None),
        working_memory=registry,
        lineage=SessionLineage(session_id="s"),
        manifest_ref=ManifestRef.from_content({"name": "a"}),
        snapshot_tree=snap_tree,
        spec_revision=4,
    )
    snap = session.take_snapshot(label="rev")
    store = JsonCheckpointStore(tmp_path)
    path = persist(
        snap,
        store,
        manifest=session.manifest_ref.content,
        lineage={
            "session_id": "s",
            "parent_session_id": None,
            "forked_from_checkpoint": None,
            "root_session_id": "s",
            "created_at": session.lineage.created_at,
        },
    )
    payload = store.load_payload(path)
    assert payload["spec_revision"] == 4
    assert payload["version"] == 2
