#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Transport-agnostic telemetry channel (in-proc + durable log)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal, Protocol

from mas.lab.benchmark.pipeline.digest import rolling_digest

logger = logging.getLogger(__name__)

OverflowPolicy = Literal["block", "drop-newest"]

_current_channel: ContextVar["TelemetryChannel | None"] = ContextVar(
    "mas_pipeline_channel", default=None
)
_current_stream_id: ContextVar[str | None] = ContextVar(
    "mas_pipeline_stream_id", default=None
)
_PROCESS: "TelemetryChannel | None" = None


@dataclass(frozen=True)
class StreamFrame:
    stream_id: str
    offset: int
    digest: str
    kind: str
    body: dict[str, Any]
    first_offset: int = 0
    n: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "offset": self.offset,
            "digest": self.digest,
            "kind": self.kind,
            "body": self.body,
            "first_offset": self.first_offset,
            "n": self.n,
        }


class TelemetryChannel(Protocol):
    async def publish(self, stream_id: str, record: dict[str, Any]) -> int: ...

    def publish_sync(self, stream_id: str, record: dict[str, Any]) -> int: ...

    def subscribe(
        self,
        selector: str | None = None,
        *,
        from_offset: int | str | dict[str, int] = 0,
        filter: Callable[[StreamFrame], bool] | None = None,
        overflow: OverflowPolicy = "block",
        bound: int = 1024,
    ) -> AsyncIterator[StreamFrame]: ...

    def subscribe_batches(
        self,
        selector: str | None = None,
        *,
        from_offset: int | str | dict[str, int] = 0,
        filter: Callable[[StreamFrame], bool] | None = None,
        overflow: OverflowPolicy = "block",
        bound: int = 1024,
    ) -> AsyncIterator[list[StreamFrame]]: ...

    async def seal(self, stream_id: str) -> str: ...

    def seal_sync(self, stream_id: str) -> str: ...

    def is_sealed(self, stream_id: str) -> bool: ...

    def replay(self, stream_id: str) -> list[StreamFrame]: ...

    def final_digest(self, stream_id: str) -> str | None: ...


@dataclass
class _StreamState:
    records: list[StreamFrame] = field(default_factory=list)
    digest: str = ""
    sealed: bool = False
    waiters: list[asyncio.Event] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class InprocChannel:
    """Thread-safe in-process channel. Producers may be sync (MAS worker threads)."""

    def __init__(self) -> None:
        self._streams: dict[str, _StreamState] = defaultdict(_StreamState)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bound = 1 << 20

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_event_loop()

    def _state(self, stream_id: str) -> _StreamState:
        return self._streams[stream_id]

    def publish_sync(self, stream_id: str, record: dict[str, Any]) -> int:
        body = dict(record)
        kind = str(body.get("kind") or "record")
        state = self._state(stream_id)
        with state.lock:
            if state.sealed:
                raise RuntimeError(f"stream {stream_id} is sealed")
            offset = len(state.records)
            digest = rolling_digest(state.digest, body)
            frame = StreamFrame(
                stream_id=stream_id,
                offset=offset,
                digest=digest,
                kind=kind,
                body=body,
                first_offset=offset,
                n=1,
            )
            state.records.append(frame)
            state.digest = digest
            waiters = list(state.waiters)
        self._wake(waiters)
        return offset

    async def publish(self, stream_id: str, record: dict[str, Any]) -> int:
        return self.publish_sync(stream_id, record)

    def _wake(self, waiters: list[asyncio.Event]) -> None:
        loop = self._loop
        for event in waiters:
            try:
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(event.set)
                else:
                    event.set()
            except Exception:
                logger.debug("channel wake failed", exc_info=True)

    def subscribe(
        self,
        selector: str | None = None,
        *,
        from_offset: int | str | dict[str, int] = 0,
        filter: Callable[[StreamFrame], bool] | None = None,
        overflow: OverflowPolicy = "block",
        bound: int = 1024,
    ) -> AsyncIterator[StreamFrame]:
        stream_id = selector or ""
        start = 0
        if isinstance(from_offset, int):
            start = from_offset
        elif from_offset == "live":
            state = self._state(stream_id)
            with state.lock:
                start = len(state.records)
        elif isinstance(from_offset, dict):
            start = int(from_offset.get(stream_id, 0))
        return self._iter_stream(stream_id, start, filter, overflow, bound)

    def subscribe_batches(
        self,
        selector: str | None = None,
        *,
        from_offset: int | str | dict[str, int] = 0,
        filter: Callable[[StreamFrame], bool] | None = None,
        overflow: OverflowPolicy = "block",
        bound: int = 1024,
    ) -> AsyncIterator[list[StreamFrame]]:
        stream_id = selector or ""
        start = 0
        if isinstance(from_offset, int):
            start = from_offset
        elif from_offset == "live":
            state = self._state(stream_id)
            with state.lock:
                start = len(state.records)
        elif isinstance(from_offset, dict):
            start = int(from_offset.get(stream_id, 0))
        return self._iter_batches(stream_id, start, filter, overflow, bound)

    async def _iter_batches(
        self,
        stream_id: str,
        start: int,
        filt: Callable[[StreamFrame], bool] | None,
        overflow: OverflowPolicy,
        bound: int,
    ) -> AsyncIterator[list[StreamFrame]]:
        state = self._state(stream_id)
        cursor = start
        while True:
            available: list[StreamFrame] = []
            sealed = False
            with state.lock:
                if cursor < len(state.records):
                    available = state.records[cursor:]
                    cursor = len(state.records)
                sealed = state.sealed and cursor >= len(state.records)
                if not available and not sealed:
                    event = asyncio.Event()
                    state.waiters.append(event)
                else:
                    event = None
            out: list[StreamFrame] = []
            for frame in available:
                if filt is not None and not filt(frame):
                    continue
                if overflow == "drop-newest" and len(out) >= bound:
                    continue
                out.append(frame)
            if out:
                yield out
            if sealed:
                return
            if event is not None:
                await event.wait()
                with state.lock:
                    if event in state.waiters:
                        state.waiters.remove(event)

    async def _iter_stream(
        self,
        stream_id: str,
        start: int,
        filt: Callable[[StreamFrame], bool] | None,
        overflow: OverflowPolicy,
        bound: int,
    ) -> AsyncIterator[StreamFrame]:
        async for batch in self._iter_batches(stream_id, start, filt, overflow, bound):
            for frame in batch:
                yield frame

    def is_sealed(self, stream_id: str) -> bool:
        state = self._streams.get(stream_id)
        if state is None:
            return False
        with state.lock:
            return bool(state.sealed)

    def seal_sync(self, stream_id: str) -> str:
        state = self._state(stream_id)
        with state.lock:
            state.sealed = True
            digest = state.digest
            waiters = list(state.waiters)
        self._wake(waiters)
        return digest

    async def seal(self, stream_id: str) -> str:
        return self.seal_sync(stream_id)

    def final_digest(self, stream_id: str) -> str | None:
        state = self._streams.get(stream_id)
        if state is None:
            return None
        with state.lock:
            if not state.sealed:
                return None
            return state.digest

    def replay(self, stream_id: str) -> list[StreamFrame]:
        state = self._streams.get(stream_id)
        if state is None:
            return []
        with state.lock:
            return list(state.records)


class LogChannel:
    """Durable segment store that also serves from_offset to late subscribers."""

    def __init__(self, root: Path, inner: InprocChannel | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.inner = inner or InprocChannel()

    def _path(self, stream_id: str) -> Path:
        safe = stream_id.replace("/", "_").replace("#", "_")
        return self.root / f"{safe}.jsonl"

    def publish_sync(self, stream_id: str, record: dict[str, Any]) -> int:
        offset = self.inner.publish_sync(stream_id, record)
        frame = self.inner.replay(stream_id)[offset]
        path = self._path(stream_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(frame.as_dict(), ensure_ascii=False, default=str) + "\n")
        return offset

    async def publish(self, stream_id: str, record: dict[str, Any]) -> int:
        return self.publish_sync(stream_id, record)

    def subscribe(self, selector=None, **kwargs):
        return self.inner.subscribe(selector, **kwargs)

    def subscribe_batches(self, selector=None, **kwargs):
        return self.inner.subscribe_batches(selector, **kwargs)

    def seal_sync(self, stream_id: str) -> str:
        digest = self.inner.seal_sync(stream_id)
        (self.root / f"{stream_id.replace('/', '_').replace('#', '_')}.digest").write_text(
            digest, encoding="utf-8"
        )
        return digest

    async def seal(self, stream_id: str) -> str:
        return self.seal_sync(stream_id)

    def final_digest(self, stream_id: str) -> str | None:
        return self.inner.final_digest(stream_id)

    def is_sealed(self, stream_id: str) -> bool:
        return self.inner.is_sealed(stream_id)

    def replay(self, stream_id: str) -> list[StreamFrame]:
        return self.inner.replay(stream_id)

    def load_file(self, stream_id: str) -> list[StreamFrame]:
        path = self._path(stream_id)
        if not path.is_file():
            return []
        out: list[StreamFrame] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            out.append(
                StreamFrame(
                    stream_id=data["stream_id"],
                    offset=int(data["offset"]),
                    digest=data["digest"],
                    kind=data["kind"],
                    body=data["body"],
                    first_offset=int(data.get("first_offset") or data["offset"]),
                    n=int(data.get("n") or 1),
                )
            )
        return out


class CompositeChannel:
    """inproc + log: publish to both; subscribe from inproc (which has the log's replay)."""

    def __init__(self, inproc: InprocChannel, log: LogChannel | None = None) -> None:
        self.inproc = inproc
        self.log = log

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.inproc.bind_loop(loop)

    def publish_sync(self, stream_id: str, record: dict[str, Any]) -> int:
        if self.log is not None:
            return self.log.publish_sync(stream_id, record)
        return self.inproc.publish_sync(stream_id, record)

    async def publish(self, stream_id: str, record: dict[str, Any]) -> int:
        return self.publish_sync(stream_id, record)

    def subscribe(self, selector=None, **kwargs):
        return self.inproc.subscribe(selector, **kwargs)

    def subscribe_batches(self, selector=None, **kwargs):
        return self.inproc.subscribe_batches(selector, **kwargs)

    def seal_sync(self, stream_id: str) -> str:
        if self.log is not None:
            return self.log.seal_sync(stream_id)
        return self.inproc.seal_sync(stream_id)

    async def seal(self, stream_id: str) -> str:
        return self.seal_sync(stream_id)

    def final_digest(self, stream_id: str) -> str | None:
        return self.inproc.final_digest(stream_id)

    def is_sealed(self, stream_id: str) -> bool:
        return self.inproc.is_sealed(stream_id)

    def replay(self, stream_id: str) -> list[StreamFrame]:
        return self.inproc.replay(stream_id)


def native_body(frame: StreamFrame | dict[str, Any]) -> dict[str, Any]:
    """Unwrap a channel frame (or raw dict) to the native record body."""
    body = frame.body if isinstance(frame, StreamFrame) else frame
    if not isinstance(body, dict):
        return {}
    inner = body.get("record")
    if isinstance(inner, dict) and "kind" not in body:
        return inner
    return body


def frame_at(channel: TelemetryChannel, stream_id: str, offset: int) -> StreamFrame | None:
    frames = channel.replay(stream_id)
    if 0 <= offset < len(frames):
        return frames[offset]
    return None


def install_channel(settings: Any | None = None, *, cache_root: Path | None = None) -> TelemetryChannel:
    """Install a process-wide channel if one is not already bound."""
    del settings
    existing = get_channel()
    if existing is not None:
        return existing
    base = Path(cache_root) if cache_root is not None else Path.home() / ".cache" / "mas-lab"
    channel = make_channel(log_root=Path(base) / "streams")
    try:
        channel.bind_loop(asyncio.get_event_loop())
    except Exception:
        logger.debug("channel loop bind skipped", exc_info=True)
    set_channel(channel)
    return channel


def get_channel() -> TelemetryChannel | None:
    return _current_channel.get() or _PROCESS


def set_channel(channel: TelemetryChannel | None) -> Token:
    global _PROCESS
    _PROCESS = channel
    return _current_channel.set(channel)


def reset_channel(token: Token) -> None:
    _current_channel.reset(token)


def get_stream_id() -> str | None:
    return _current_stream_id.get()


def set_stream_id(stream_id: str | None) -> Token:
    return _current_stream_id.set(stream_id)


def reset_stream_id(token: Token) -> None:
    _current_stream_id.reset(token)


def make_channel(*, log_root: Path | None = None) -> CompositeChannel:
    inproc = InprocChannel()
    log = LogChannel(log_root, inner=inproc) if log_root is not None else None
    return CompositeChannel(inproc, log)
