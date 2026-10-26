#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON-lines unix-socket adapter over ControlContract.

Same verbs as in-process ``SessionControl``. The wire is not a second
implementation: the server calls the local contract. This is the
smallest local cross-process surface; it is not a distributed attach.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from mas.runtime.boundary.control.contract import ControlDenied, SessionPaused
from mas.runtime.session.snapshot import SnapshotRef


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "snapshot_id"):
        return {
            "snapshot_id": value.snapshot_id,
            "session_id": getattr(value, "session_id", ""),
            "parent_snapshot_id": getattr(value, "parent_snapshot_id", None),
            "turn": getattr(value, "turn", 0),
            "label": getattr(value, "label", ""),
            "kind": getattr(value, "kind", ""),
        }
    return str(value)


class ControlRpcServer:
    """Serve ``ControlContract`` over a unix-domain socket (JSON lines)."""

    def __init__(self, control: Any, path: str | Path) -> None:
        self.control = control
        self.path = Path(path)
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.path.exists():
            self.path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    request = json.loads(raw.decode())
                    result = await asyncio.to_thread(self._dispatch, request)
                    reply = {"id": request.get("id"), "ok": True, "result": _jsonable(result)}
                except Exception as exc:
                    reply = {
                        "id": (request.get("id") if isinstance(locals().get("request"), dict) else None),
                        "ok": False,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def _dispatch(self, request: dict[str, Any]) -> Any:
        method = str(request.get("method") or "")
        session_id = str(request.get("session_id") or "")
        args = dict(request.get("args") or {})
        fn = getattr(self.control, method, None)
        if not callable(fn):
            raise AttributeError(f"unknown control method {method!r}")
        if method in {
            "pause",
            "steer",
            "navigate",
            "enqueue_input",
            "disable_tool",
        }:
            return fn(session_id, **args)
        if method in {"cancel_queued"}:
            return fn(session_id, str(args.get("input_id") or ""))
        if method in {"reorder_queue"}:
            return fn(session_id, list(args.get("order") or []))
        return fn(session_id)


class ControlRpcClient:
    """ControlContract client. Reconstructs SnapshotRef for list/navigate."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_id = 0

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.path)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def call(self, method: str, session_id: str, **args: Any) -> Any:
        if self._reader is None or self._writer is None:
            raise RuntimeError("ControlRpcClient is not connected")
        self._next_id += 1
        request = {"id": self._next_id, "method": method, "session_id": session_id, "args": args}
        self._writer.write((json.dumps(request) + "\n").encode())
        await self._writer.drain()
        raw = await self._reader.readline()
        reply = json.loads(raw.decode() or "{}")
        if not reply.get("ok"):
            err = reply.get("error") or {}
            name = str(err.get("type") or "Error")
            message = str(err.get("message") or "rpc failed")
            if name == "ControlDenied":
                raise ControlDenied(method, session_id, reason=message)
            if name == "SessionPaused":
                raise SessionPaused(session_id, reason=message)
            raise RuntimeError(f"{name}: {message}")
        return reply.get("result")

    def pause(self, session_id: str, *, reason: str) -> None:
        raise RuntimeError("use apause — this client is async")

    async def apause(self, session_id: str, *, reason: str) -> None:
        await self.call("pause", session_id, reason=reason)

    async def aresume(self, session_id: str) -> None:
        await self.call("resume", session_id)

    async def ainspect(self, session_id: str) -> dict[str, Any]:
        return dict(await self.call("inspect", session_id) or {})

    async def ainspect_context(self, session_id: str) -> dict[str, Any]:
        """Return the latest context usage view without prompt contents."""
        return dict(await self.call("inspect_context", session_id) or {})

    async def ainspect_archive(self, session_id: str) -> dict[str, Any]:
        """Dump the conversation archive covering tree for debug."""
        return dict(await self.call("inspect_archive", session_id) or {})

    async def alist_checkpoints(self, session_id: str) -> list[SnapshotRef]:
        rows = await self.call("list_checkpoints", session_id) or []
        return [_ref_from_payload(item, session_id) for item in rows]

    async def anavigate(self, session_id: str, *, to: str, reason: str) -> SnapshotRef:
        payload = await self.call("navigate", session_id, to=to, reason=reason)
        return _ref_from_payload(payload, session_id)

    async def acancel_inflight(self, session_id: str) -> bool:
        return bool(await self.call("cancel_inflight", session_id))


def _ref_from_payload(payload: Any, session_id: str) -> SnapshotRef:
    if isinstance(payload, SnapshotRef):
        return payload
    data = dict(payload or {})
    return SnapshotRef(
        snapshot_id=str(data.get("snapshot_id") or ""),
        session_id=str(data.get("session_id") or session_id),
        parent_snapshot_id=data.get("parent_snapshot_id"),
        turn=int(data.get("turn") or 0),
        taken_at=str(data.get("taken_at") or ""),
        spec_revision=data.get("spec_revision"),
        label=str(data.get("label") or ""),
        kind=str(data.get("kind") or ""),
    )
