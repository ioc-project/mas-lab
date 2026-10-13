#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

import pytest

from mas.lab.benchmark.pipeline.channel import make_channel
from mas.lab.benchmark.pipeline.ws import check_token, consume_stream, serve_channel


@pytest.mark.asyncio
async def test_v2_ws_roundtrip_and_auth() -> None:
    ch = make_channel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "e/s/i/r1#1"
    ch.publish_sync(sid, {"kind": "llm_call_end"})
    await ch.seal(sid)
    server, endpoint = await serve_channel(ch, token="secret")
    host_port = endpoint.rsplit(":", 1)
    host = host_port[0].replace("ws://", "").replace("wss://", "")
    port = int(host_port[1])
    with pytest.raises(PermissionError):
        await consume_stream(host, port, sid, token="nope", idle_timeout=1.0)
    frames = await consume_stream(host, port, sid, token="secret", idle_timeout=1.0)
    assert frames and frames[0].kind == "llm_call_end"
    server.close()
    await server.wait_closed()
    assert check_token("secret", "secret")
    assert not check_token("nope", "secret")


@pytest.mark.asyncio
async def test_v2_ws_resume_from_offset() -> None:
    ch = make_channel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "e/s/i/r1#1"
    for i in range(4):
        ch.publish_sync(sid, {"kind": "x", "i": i})
    await ch.seal(sid)
    server, endpoint = await serve_channel(ch, token="t")
    host = endpoint.rsplit(":", 1)[0].replace("ws://", "")
    port = int(endpoint.rsplit(":", 1)[1])
    first = await consume_stream(host, port, sid, token="t", from_offset=0, max_frames=2, idle_timeout=1.0)
    assert [f.offset for f in first] == [0, 1]
    rest = await consume_stream(host, port, sid, token="t", from_offset=2, idle_timeout=1.0)
    assert [f.offset for f in rest] == [2, 3]
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_v2_ws_fold_equals_inproc() -> None:
    from mas.library.lab.steps.extract.trace_stats import (
        empty_trace_stats,
        fold_trace_event,
        public_trace_stats,
    )

    events = [
        {"kind": "llm_call_start"},
        {"kind": "llm_call_end", "tokens_used": {"total_tokens": 4, "prompt_tokens": 3, "completion_tokens": 1}},
        {"kind": "tool_call_start"},
        {"kind": "execution_end"},
    ]
    ch = make_channel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "e/s/i/r1#1"
    for rec in events:
        ch.publish_sync(sid, rec)
    await ch.seal(sid)

    inproc_stats = empty_trace_stats()
    for rec in events:
        fold_trace_event(inproc_stats, rec)
    inproc = public_trace_stats(inproc_stats)

    server, endpoint = await serve_channel(ch, token="t")
    host = endpoint.rsplit(":", 1)[0].replace("ws://", "")
    port = int(endpoint.rsplit(":", 1)[1])
    frames = await consume_stream(host, port, sid, token="t", idle_timeout=1.0)
    server.close()
    await server.wait_closed()
    cold = empty_trace_stats()
    for frame in frames:
        fold_trace_event(cold, frame.body)
    assert public_trace_stats(cold) == inproc


@pytest.mark.asyncio
async def test_v2_ws_require_tls_non_loopback() -> None:
    ch = make_channel()
    with pytest.raises(RuntimeError, match="TLS required"):
        await serve_channel(ch, host="0.0.0.0", require_tls=True)
