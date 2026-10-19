#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON-lines unix/TCP adapter over ControlContract (library plugin).

This is a control-protocol wire, not A2A. Two people talking to an agent
use A2A ``contextId`` as the session id. This plugin is how an operator
host attaches to ``ControlContract`` across processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from mas.runtime.boundary.control.contract import ControlDenied, SessionNotStopped, SessionPaused
from mas.runtime.session.snapshot import SnapshotRef

_KWARGS_METHODS = frozenset(
    {
        "pause",
        "steer",
        "navigate",
        "enqueue_input",
        "send_message",
        "disable_tool",
        "set_queued_action",
        "snapshot",
        "persist",
        "run_script",
    }
)
_POSITIONAL_AFTER_SESSION = {
    "cancel_queued": "input_id",
    "reorder_queue": "order",
}


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


def parse_endpoint(endpoint: str) -> tuple[str, str | None, int | None]:
    if endpoint.startswith("unix:"):
        return "unix", endpoint[5:], None
    if endpoint.startswith("tcp:"):
        rest = endpoint[4:]
        host, sep, port_text = rest.rpartition(":")
        if not sep or not host:
            raise ValueError(f"invalid tcp endpoint {endpoint!r}")
        return "tcp", host, int(port_text)
    raise ValueError(f"endpoint must start with unix: or tcp: ({endpoint!r})")


def _tokens_match(expected: str, given: str) -> bool:
    if not expected:
        return True
    left = hashlib.sha256(expected.encode()).digest()
    right = hashlib.sha256(given.encode()).digest()
    return hmac.compare_digest(left, right)


class ControlRpcServer:
    def __init__(
        self,
        control: Any,
        path: str | Path | None = None,
        *,
        host: str | None = None,
        port: int = 0,
        token: str = "",
        hub: Any | None = None,
    ) -> None:
        if path is not None and host is not None:
            raise ValueError("use either a unix path or a tcp host, not both")
        if path is None and host is None:
            raise ValueError("unix path or tcp host is required")
        self.control = control
        self.path = Path(path) if path is not None else None
        self.host = host
        self.port = int(port)
        self.token = token
        self._server: asyncio.AbstractServer | None = None
        self._bound: tuple[str, int] | None = None
        self._hub = hub

    @property
    def endpoint(self) -> str:
        if self.path is not None:
            return f"unix:{self.path}"
        if self._bound is None:
            raise RuntimeError("ControlRpcServer is not started")
        host, port = self._bound
        return f"tcp:{host}:{port}"

    async def start(self) -> None:
        if self.path is not None:
            if self.path.exists():
                self.path.unlink()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
            return
        self._server = await asyncio.start_server(self._handle, host=self.host, port=self.port)
        sock = self._server.sockets[0]
        got = sock.getsockname()
        self._bound = (str(got[0]), int(got[1]))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._bound = None
        if self.path is not None and self.path.exists():
            self.path.unlink()

    def _require_hub(self) -> Any:
        if self._hub is None:
            raise RuntimeError("exchange hub is not configured")
        return self._hub

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                request: dict[str, Any] | None = None
                try:
                    request = json.loads(raw.decode())
                    method = str((request or {}).get("method") or "")
                    if method == "subscribe_exchange":
                        await self._stream_exchanges(request, reader, writer)
                        return
                    result = await asyncio.to_thread(self._dispatch, request)
                    reply = {"id": request.get("id"), "ok": True, "result": _jsonable(result)}
                except Exception as exc:
                    reply = {
                        "id": (request.get("id") if isinstance(request, dict) else None),
                        "ok": False,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _stream_exchanges(
        self,
        request: dict[str, Any],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        session_id = str(request.get("session_id") or "")
        if not _tokens_match(self.token, str(request.get("token") or "")):
            raise ControlDenied("subscribe_exchange", session_id, reason="attach token mismatch")
        hub = self._require_hub()
        queue = hub.subscribe(session_id)
        buffered = hub.buffer(session_id)
        writer.write(
            (
                json.dumps(
                    {
                        "id": request.get("id"),
                        "ok": True,
                        "result": {"subscribed": True, "buffered": buffered},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        read_task = asyncio.create_task(reader.readline())
        get_task = asyncio.create_task(queue.get())
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {read_task, get_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if read_task in done:
                    raw = read_task.result()
                    if not raw:
                        break
                    read_task = asyncio.create_task(reader.readline())
                    continue
                item = get_task.result()
                get_task = asyncio.create_task(queue.get())
                writer.write(
                    (
                        json.dumps(
                            {
                                "type": "exchange",
                                "session_id": session_id,
                                "replay": bool(item.get("replay")),
                                "record": item.get("record") or {},
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
        finally:
            read_task.cancel()
            get_task.cancel()
            for task in (read_task, get_task):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            hub.unsubscribe(session_id, queue)

    def _dispatch(self, request: dict[str, Any]) -> Any:
        method = str(request.get("method") or "")
        session_id = str(request.get("session_id") or "")
        args = dict(request.get("args") or {})
        if not _tokens_match(self.token, str(request.get("token") or "")):
            raise ControlDenied(method, session_id, reason="attach token mismatch")
        fn = getattr(self.control, method, None)
        if method.startswith("_") or not callable(fn):
            raise AttributeError(f"unknown control method {method!r}")
        if method in _KWARGS_METHODS:
            return fn(session_id, **args)
        positional = _POSITIONAL_AFTER_SESSION.get(method)
        if positional is not None:
            value = args.get(positional)
            if method == "reorder_queue":
                kwargs = {"order": list(value or [])}
                if "revision" in args:
                    kwargs["revision"] = args.get("revision")
                return fn(session_id, **kwargs) if "revision" in kwargs else fn(session_id, list(value or []))
            kwargs = {positional: str(value or "")}
            if "revision" in args:
                kwargs["revision"] = args.get("revision")
            return fn(session_id, **kwargs)
        return fn(session_id)


class ControlRpcClient:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        token: str = "",
    ) -> None:
        if path is not None and host is not None:
            raise ValueError("use either a unix path or a tcp host, not both")
        if path is None and (host is None or port is None):
            raise ValueError("unix path or tcp host and port is required")
        self.path = str(path) if path is not None else None
        self.host = host
        self.port = int(port) if port is not None else None
        self.token = token
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_id = 0

    @classmethod
    def from_endpoint(cls, endpoint: str, *, token: str = "") -> ControlRpcClient:
        kind, locator, port = parse_endpoint(endpoint)
        if kind == "unix":
            return cls(locator, token=token)
        return cls(host=locator, port=port, token=token)

    async def connect(self) -> None:
        if self.path is not None:
            self._reader, self._writer = await asyncio.open_unix_connection(self.path)
            return
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

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
        request = {
            "id": self._next_id,
            "method": method,
            "session_id": session_id,
            "args": args,
            "token": self.token,
        }
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
            if name == "SessionNotStopped":
                raise SessionNotStopped(session_id, method=method)
            raise RuntimeError(f"{name}: {message}")
        return reply.get("result")

    async def asubscribe_exchange(self, session_id: str):
        """Yield live exchange dicts. Catch-up rows have replay=True."""
        if self._reader is None or self._writer is None:
            raise RuntimeError("ControlRpcClient is not connected")
        self._next_id += 1
        request = {
            "id": self._next_id,
            "method": "subscribe_exchange",
            "session_id": session_id,
            "args": {},
            "token": self.token,
        }
        self._writer.write((json.dumps(request) + "\n").encode())
        await self._writer.drain()
        raw = await self._reader.readline()
        reply = json.loads(raw.decode() or "{}")
        if not reply.get("ok"):
            err = reply.get("error") or {}
            raise RuntimeError(str(err.get("message") or "subscribe_exchange failed"))
        for item in list((reply.get("result") or {}).get("buffered") or []):
            yield dict(item, replay=True)
        while True:
            raw = await self._reader.readline()
            if not raw:
                break
            payload = json.loads(raw.decode() or "{}")
            if payload.get("type") != "exchange":
                continue
            yield payload

    async def ainspect(self, session_id: str) -> dict[str, Any]:
        return dict(await self.call("inspect", session_id) or {})

    async def aenqueue_input(
        self,
        session_id: str,
        *,
        text: str,
        source: str,
        priority: int = 0,
        action: str = "turn",
        at: int | str = "tail",
    ) -> str:
        return str(
            await self.call(
                "enqueue_input",
                session_id,
                text=text,
                source=source,
                priority=priority,
                action=action,
                at=at,
            )
        )

    async def asend_message(
        self,
        session_id: str,
        *,
        text: str,
        source: str = "user",
    ) -> str:
        return str(
            await self.call("send_message", session_id, text=text, source=source)
        )

    async def asteer(
        self,
        session_id: str,
        *,
        text: str,
        mode: str = "preempt",
        at: int | str = "head",
    ) -> None:
        await self.call("steer", session_id, text=text, mode=mode, at=at)

    async def ainspect_queue(self, session_id: str) -> dict[str, Any]:
        return dict(await self.call("inspect_queue", session_id) or {})

    async def apause(self, session_id: str, *, reason: str) -> None:
        await self.call("pause", session_id, reason=reason)

    async def aresume(self, session_id: str) -> None:
        await self.call("resume", session_id)

    async def alist_checkpoints(self, session_id: str) -> list[Any]:
        result = await self.call("list_checkpoints", session_id)
        return list(result or [])

    async def asnapshot(self, session_id: str, *, label: str = "", auto_stop: bool = False) -> dict[str, Any]:
        return dict(
            await self.call("snapshot", session_id, label=label, auto_stop=auto_stop) or {}
        )

    async def apersist(
        self,
        session_id: str,
        *,
        snapshot_id: str | None = None,
        label: str = "",
        auto_stop: bool = False,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"label": label, "auto_stop": auto_stop}
        if snapshot_id:
            args["snapshot_id"] = snapshot_id
        return dict(await self.call("persist", session_id, **args) or {})

    async def arun_script(
        self,
        session_id: str,
        *,
        text: str = "",
        script_file: str = "",
        auto_stop: bool = False,
    ) -> list[Any]:
        result = await self.call(
            "run_script",
            session_id,
            text=text,
            script_file=script_file,
            auto_stop=auto_stop,
        )
        return list(result or [])


class ControlRpcProtocol:
    """Registry plugin: control-protocol wire over ``ControlContract``."""

    plugin_id = "rpc"
    kind = "rpc"

    def server(
        self,
        control: Any,
        *,
        path: str | Path | None = None,
        host: str | None = None,
        port: int = 0,
        token: str = "",
        hub: Any | None = None,
    ) -> ControlRpcServer:
        return ControlRpcServer(control, path, host=host, port=port, token=token, hub=hub)
