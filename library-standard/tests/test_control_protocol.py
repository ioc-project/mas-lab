#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.library.standard.plugins.control.attach import (
    HostGone,
    attach,
    recover_after_host_gone,
    resolve_attach,
    serve_and_advertise,
)
from mas.library.standard.plugins.control.directory import FileSessionDirectory, SessionAdvertisement
from mas.library.standard.plugins.control.rpc import ControlRpcClient, ControlRpcProtocol
from mas.runtime.boundary.control.contract import ControlCapability, ControlDenied
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.session.snapshot import SnapshotTree


def _reaped_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return int(proc.pid)


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

    controller = SessionController(instance=instance, display=_Display())
    manager = SessionManager(snapshot_tree=SnapshotTree())
    manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id="s1")
    return manager


def test_control_protocol_is_a_library_plugin() -> None:
    from mas.runtime.harness.catalog import classify_plugin_type

    assert classify_plugin_type("control_protocol") == "library"
    assert ControlRpcProtocol().plugin_id == "rpc"


def test_checkpoint_store_is_a_library_plugin() -> None:
    from mas.library.standard.plugins.checkpoint import (
        HybridCheckpointStore,
        InMemoryCheckpointStore,
        JsonCheckpointStore,
    )
    from mas.runtime.harness.catalog import LIBRARY_TYPES, classify_plugin_type

    assert "checkpoint_store" in LIBRARY_TYPES
    assert classify_plugin_type("checkpoint_store") == "library"
    assert InMemoryCheckpointStore.plugin_id == "memory"
    assert JsonCheckpointStore.plugin_id == "disk"
    assert HybridCheckpointStore.plugin_id == "hybrid"


@pytest.mark.asyncio
async def test_attach_over_tcp_enqueues(tmp_path: Path) -> None:
    manager = _manager()
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    directory = FileSessionDirectory(tmp_path)
    server, hosted = await serve_and_advertise(control, directory, "s1", host="127.0.0.1")
    client = await attach(directory, "s1")
    try:
        queued = await client.aenqueue_input("s1", text="hello", source="peer")
        ahead = await client.aenqueue_input("s1", text="urgent", source="peer", at="head")
        mid = await client.aenqueue_input("s1", text="mid", source="peer", at=1)
        view = await client.ainspect_queue("s1")
        assert queued and ahead and mid
        assert [item["text"] for item in view["items"]] == ["urgent", "mid", "hello"]
        sent = await client.asend_message("s1", text="also from a2a shape")
        view = await client.ainspect_queue("s1")
        assert sent
        assert view["items"][-1]["text"] == "also from a2a shape"
        assert view["items"][-1]["action"] == "turn"
        await client.asteer("s1", text="now", mode="after")
        view = await client.ainspect_queue("s1")
        assert view["items"][0]["text"] == "now"
        assert view["items"][0]["action"] == "turn"
    finally:
        await client.close()
        hosted.unadvertise()
        await server.close()


@pytest.mark.asyncio
async def test_wrong_attach_token_is_denied(tmp_path: Path) -> None:
    manager = _manager()
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    directory = FileSessionDirectory(tmp_path)
    server, hosted = await serve_and_advertise(control, directory, "s1", host="127.0.0.1")
    advertisement = directory.lookup("s1")
    assert advertisement is not None
    client = ControlRpcClient.from_endpoint(advertisement.rpc, token="not-the-token")
    await client.connect()
    try:
        with pytest.raises(ControlDenied):
            await client.ainspect("s1")
    finally:
        await client.close()
        hosted.unadvertise()
        await server.close()


def test_stale_heartbeat_is_not_live(tmp_path: Path) -> None:
    directory = FileSessionDirectory(tmp_path, ttl=timedelta(seconds=1))
    directory.advertise(
        SessionAdvertisement(
            session_id="s1",
            rpc="unix:/tmp/s1.sock",
            heartbeat_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
        )
    )
    assert directory.is_live("s1") is False


def test_host_gone_without_persist_fails_closed(tmp_path: Path) -> None:
    directory = FileSessionDirectory(tmp_path)
    directory.advertise(SessionAdvertisement(session_id="s1", rpc="tcp:127.0.0.1:9", pid=_reaped_pid()))
    with pytest.raises(HostGone) as caught:
        resolve_attach(directory, "s1")
    with pytest.raises(HostGone):
        recover_after_host_gone(caught.value, SessionManager())


@pytest.mark.asyncio
async def test_attach_by_session_id_can_snapshot_pause_and_steer(tmp_path: Path) -> None:
    manager = _manager()
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    directory = FileSessionDirectory(tmp_path)
    server, hosted = await serve_and_advertise(control, directory, "s1", host="127.0.0.1")
    client = await attach(directory, "s1")
    try:
        await client.apause("s1", reason="freeze")
        ref = await client.asnapshot("s1", label="before-steer", auto_stop=True)
        await client.aresume("s1")
        await client.asteer("s1", text="Answer Lyon.", mode="enqueue")
        nodes = await client.alist_checkpoints("s1")
        view = await client.ainspect("s1")
        assert ref["label"] == "before-steer"
        assert view["session_id"] == "s1"
        assert any(node["snapshot_id"] == ref["snapshot_id"] for node in nodes)
        queue = await client.ainspect_queue("s1")
        assert queue["items"][0]["action"] == "turn"
    finally:
        await client.close()
        hosted.unadvertise()
        await server.close()


@pytest.mark.asyncio
async def test_persist_writes_checkpoint_file_for_resume(tmp_path: Path) -> None:
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore

    manager = _manager()
    store = JsonCheckpointStore(tmp_path / "ckpts")
    manager.checkpoint_store = store
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    directory = FileSessionDirectory(tmp_path / "dir")
    server, hosted = await serve_and_advertise(control, directory, "s1", host="127.0.0.1")
    client = await attach(directory, "s1")
    try:
        saved = await client.apersist("s1", label="before-steer", auto_stop=True)
        await client.asteer("s1", text="Answer Lyon.", mode="enqueue")
        payload = store.load_payload(Path(saved["path"]))
        assert saved["label"] == "before-steer"
        assert payload["version"] == 2
        assert "Lyon" not in str(payload.get("working_memory"))
    finally:
        await client.close()
        hosted.unadvertise()
        await server.close()


@pytest.mark.asyncio
async def test_subscribe_exchange_replays_then_streams(tmp_path: Path) -> None:
    from mas.runtime.boundary.obs.exchange_hub import ExchangeHub

    manager = _manager()
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    directory = FileSessionDirectory(tmp_path)
    hub = ExchangeHub()
    hub.publish("s-sub-ex", {"kind": "user_in", "text": "hi"})
    server, hosted = await serve_and_advertise(
        control, directory, "s-sub-ex", host="127.0.0.1", hub=hub
    )
    client = await attach(directory, "s-sub-ex")
    seen: list[str] = []
    replayed = False
    try:
        agen = client.asubscribe_exchange("s-sub-ex")
        first = await agen.__anext__()
        seen.append(first["record"]["kind"])
        replayed = bool(first.get("replay"))
        hub.publish("s-sub-ex", {"kind": "user_out", "text": "ok"})
        second = await agen.__anext__()
        seen.append(second["record"]["kind"])
        await agen.aclose()
    finally:
        await client.close()
        hosted.unadvertise()
        await server.close()
    assert seen == ["user_in", "user_out"]
    assert replayed is True
