#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Attach to a hosted ControlContract, or fail closed and recover from persist."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.library.standard.plugins.control.directory import (
    FileSessionDirectory,
    SessionAdvertisement,
)
from mas.library.standard.plugins.control.rpc import ControlRpcClient, ControlRpcServer


class HostGone(RuntimeError):
    def __init__(self, session_id: str, *, persist_path: str = "") -> None:
        self.session_id = session_id
        self.persist_path = persist_path
        extra = f"; persist={persist_path}" if persist_path else ""
        super().__init__(f"host for session {session_id!r} is gone{extra}")


def resolve_attach(directory: FileSessionDirectory, session_id: str) -> SessionAdvertisement:
    advertisement = directory.lookup(session_id)
    if advertisement is None:
        raise KeyError(f"unknown session {session_id!r}")
    if not directory.is_live(session_id):
        raise HostGone(session_id, persist_path=advertisement.persist_path)
    return advertisement


async def attach(directory: FileSessionDirectory, session_id: str) -> ControlRpcClient:
    advertisement = resolve_attach(directory, session_id)
    client = ControlRpcClient.from_endpoint(advertisement.rpc, token=advertisement.token)
    await client.connect()
    return client


def recover_after_host_gone(
    error: HostGone,
    manager: Any,
    *,
    manifest_content: dict[str, Any] | None = None,
) -> Any:
    if not error.persist_path:
        raise error
    return manager.resume_from_checkpoint(
        Path(error.persist_path),
        manifest_content=manifest_content,
    )


@dataclass
class ControlHost:
    directory: FileSessionDirectory
    session_id: str
    rpc: str
    persist_path: str = ""
    token: str = ""
    pid: int = 0
    extra_sessions: list[str] = field(default_factory=list)

    def advertise(self) -> SessionAdvertisement:
        return self.advertise_session(self.session_id)

    def advertise_session(self, session_id: str) -> SessionAdvertisement:
        token = self.token or secrets.token_urlsafe(32)
        self.token = token
        if session_id != self.session_id and session_id not in self.extra_sessions:
            self.extra_sessions.append(session_id)
        return self.directory.advertise(
            SessionAdvertisement(
                session_id=session_id,
                rpc=self.rpc,
                persist_path=self.persist_path,
                pid=self.pid or os.getpid(),
                token=token,
            )
        )

    def heartbeat(self) -> SessionAdvertisement:
        for session_id in list(self.extra_sessions):
            try:
                self.directory.heartbeat(session_id)
            except KeyError:
                continue
        return self.directory.heartbeat(self.session_id)

    def unadvertise(self) -> None:
        self.directory.unadvertise(self.session_id)
        for session_id in list(self.extra_sessions):
            self.directory.unadvertise(session_id)


async def serve_and_advertise(
    control: Any,
    directory: FileSessionDirectory,
    session_id: str,
    *,
    persist_path: str = "",
    host: str = "127.0.0.1",
    port: int = 0,
    path: str | Path | None = None,
    token: str = "",
    hub: Any | None = None,
) -> tuple[ControlRpcServer, ControlHost]:
    attach_token = token or secrets.token_urlsafe(32)
    if path is not None:
        server = ControlRpcServer(control, path, token=attach_token, hub=hub)
    else:
        server = ControlRpcServer(control, host=host, port=port, token=attach_token, hub=hub)
    await server.start()
    hosted = ControlHost(
        directory,
        session_id,
        server.endpoint,
        persist_path=persist_path,
        token=attach_token,
    )
    hosted.advertise()
    return server, hosted
