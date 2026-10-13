#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""WebSocket transport for TelemetryChannel (sub-protocol mas-stream/1)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import ssl
from typing import Any

from mas.lab.benchmark.pipeline.channel import StreamFrame, TelemetryChannel

logger = logging.getLogger(__name__)

# RFC 6455 tokens cannot contain `/`. Hello JSON still names the contract mas-stream/1.
SUBPROTOCOL = "mas-stream-1"
CONTRACT = "mas-stream/1"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


def check_token(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(str(provided or ""), str(expected))


def _hello_token(hello: dict[str, Any], extra: str | None = None) -> str:
    if extra:
        return extra
    return str(hello.get("token") or hello.get("authorization") or "")


async def serve_channel(
    channel: TelemetryChannel,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str = "",
    require_tls: bool = False,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[Any, str]:
    """Serve ``mas-stream/1`` WebSocket frames (JSON text).

    TLS is required off loopback when ``require_tls`` is set. Auth is a hello
    ``token`` (or ``Authorization`` on the handshake). Resume uses ``from_offset``.
    Application-level ``credit`` / ``ack`` provides backpressure.
    """
    if require_tls and ssl_context is None and host not in _LOOPBACK:
        raise RuntimeError("TLS required for non-loopback stream servers")

    from websockets.asyncio.server import serve

    async def _handler(ws: Any) -> None:
        provided = ""
        request = getattr(ws, "request", None)
        headers = getattr(request, "headers", None) if request is not None else None
        if headers is not None:
            provided = str(headers.get("Authorization") or headers.get("authorization") or "")
            if provided.lower().startswith("bearer "):
                provided = provided[7:].strip()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            hello = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception:
            await ws.close(4400, "bad hello")
            return
        if not isinstance(hello, dict):
            await ws.close(4400, "bad hello")
            return
        if token and not check_token(_hello_token(hello, provided or None), token):
            await ws.send(json.dumps({"error": "unauthorized"}))
            await ws.close(4401, "unauthorized")
            return
        stream_id = str(hello.get("stream_id") or "")
        from_offset = hello.get("from_offset", 0)
        credit = max(1, int(hello.get("credit") or 32))
        outstanding = 0
        try:
            async for frame in channel.subscribe(stream_id, from_offset=from_offset):
                await ws.send(json.dumps(frame.as_dict(), default=str))
                outstanding += 1
                if outstanding >= credit:
                    try:
                        ack_raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except TimeoutError:
                        break
                    ack = json.loads(ack_raw if isinstance(ack_raw, str) else ack_raw.decode("utf-8"))
                    if ack.get("error"):
                        break
                    outstanding = 0
                    credit = max(1, int(ack.get("credit") or credit))
            while outstanding > 0:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                    outstanding = 0
                except Exception:
                    break
        except Exception:
            logger.debug("stream client closed", exc_info=True)

    server = await serve(
        _handler,
        host,
        port,
        subprotocols=[SUBPROTOCOL],
        ssl=ssl_context,
    )
    sock = server.sockets[0]
    addr = sock.getsockname()
    scheme = "wss" if ssl_context is not None else "ws"
    endpoint = f"{scheme}://{addr[0]}:{addr[1]}"
    return server, endpoint


async def consume_stream(
    host: str,
    port: int,
    stream_id: str,
    *,
    token: str = "",
    from_offset: int = 0,
    ssl_context: ssl.SSLContext | None = None,
    max_frames: int | None = None,
    idle_timeout: float = 5.0,
) -> list[StreamFrame]:
    from websockets.asyncio.client import connect

    scheme = "wss" if ssl_context is not None else "ws"
    uri = f"{scheme}://{host}:{port}"
    extra = {}
    if token:
        extra["additional_headers"] = {"Authorization": f"Bearer {token}"}
    frames: list[StreamFrame] = []
    async with connect(uri, subprotocols=[SUBPROTOCOL], ssl=ssl_context, **extra) as ws:
        await ws.send(
            json.dumps(
                {
                    "stream_id": stream_id,
                    "token": token,
                    "from_offset": from_offset,
                    "subprotocol": CONTRACT,
                    "credit": 8,
                }
            )
        )
        try:
            while max_frames is None or len(frames) < max_frames:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
                except TimeoutError:
                    break
                data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                if data.get("error"):
                    raise PermissionError(data["error"])
                frames.append(
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
                try:
                    await ws.send(json.dumps({"ack": True, "credit": 8}))
                except Exception:
                    if max_frames is not None and len(frames) >= max_frames:
                        break
                    continue
        except PermissionError:
            raise
        except Exception:
            pass
    return frames
