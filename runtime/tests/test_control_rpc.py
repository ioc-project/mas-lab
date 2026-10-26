#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.ctl.session.controller import SessionController
from mas.ctl.session.manager import SessionManager
from mas.runtime.boundary.control.contract import ControlCapability
from mas.runtime.boundary.control.rpc import ControlRpcClient, ControlRpcServer
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.session.snapshot import SnapshotTree


@pytest.mark.asyncio
async def test_control_rpc_unix_socket_list_and_navigate() -> None:
    manager = SessionManager(snapshot_tree=SnapshotTree())
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
    session = manager.create(instance, controller, {"name": "agent", "spec": {}}, session_id="s1")
    origin = session.take_snapshot(label="n0")
    session.take_snapshot(label="n1")
    control = manager.control(capability=ControlCapability(actor="rpc", surface="admin"))
    sock = Path(f"/tmp/mas-lab-ctl-{os.getpid()}.sock")
    server = ControlRpcServer(control, sock)
    await server.start()
    client = ControlRpcClient(sock)
    await client.connect()
    try:
        nodes = await client.alist_checkpoints("s1")
        ids = {n.snapshot_id for n in nodes}
        assert origin.ref.snapshot_id in ids
        moved = await client.anavigate("s1", to=origin.ref.snapshot_id, reason="rpc-walk")
        assert moved.snapshot_id == origin.ref.snapshot_id
        view = await client.ainspect("s1")
        assert view["cursor_snapshot_id"] == origin.ref.snapshot_id
        context = await client.ainspect_context("s1")
        assert context["available"] is False
        archive = await client.ainspect_archive("s1")
        assert archive.get("covers_archive") is True
        assert archive.get("covering_ids") == []
    finally:
        await client.close()
        await server.close()
