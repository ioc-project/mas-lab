#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

import pytest

from mas.lab.benchmark.pipeline.channel import InprocChannel, LogChannel, make_channel
from mas.lab.benchmark.pipeline.digest import rolling_digest
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.lifecycle import attempt_reset, begin_run, terminal_run
from mas.lab.benchmark.pipeline.channel import set_channel
from mas.lab.benchmark.pipeline.counters import reset_counters, counters


@pytest.mark.asyncio
async def test_v2_envelope_offsets_digest(tmp_path) -> None:
    ch = make_channel(log_root=tmp_path / "streams")
    sid = "exp/sc/item1/r1#1"
    bodies = [{"kind": "llm_call_end", "n": i} for i in range(5)]
    digest = ""
    for body in bodies:
        digest = rolling_digest(digest, body)
        ch.publish_sync(sid, body)
    frames = ch.inproc.replay(sid)
    assert [f.offset for f in frames] == [0, 1, 2, 3, 4]
    assert frames[-1].digest == digest
    sealed = await ch.seal(sid)
    assert sealed == digest
    ch2 = LogChannel(tmp_path / "streams", inner=InprocChannel())
    loaded = ch2.load_file(sid)
    assert [f.digest for f in loaded] == [f.digest for f in frames]


@pytest.mark.asyncio
async def test_v2_subscribe_from_offset() -> None:
    ch = InprocChannel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "s"
    for i in range(4):
        ch.publish_sync(sid, {"kind": "x", "i": i})
    seen = []

    async def consume() -> None:
        async for frame in ch.subscribe(sid, from_offset=2):
            seen.append(frame.offset)
            if frame.offset >= 3:
                break

    await asyncio.wait_for(consume(), timeout=2)
    assert seen == [2, 3]


@pytest.mark.asyncio
async def test_v2_channel_backpressure() -> None:
    ch = InprocChannel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "s"
    for i in range(5):
        ch.publish_sync(sid, {"kind": "x", "i": i})
    dropped = []

    async def consume_drop() -> None:
        async for frame in ch.subscribe(sid, from_offset=0, overflow="drop-newest", bound=2):
            dropped.append(frame.offset)
            if len(dropped) >= 2:
                break

    await asyncio.wait_for(consume_drop(), timeout=2)
    assert dropped == [0, 1]


@pytest.mark.asyncio
async def test_v2_channel_backpressure_block_never_drops() -> None:
    ch = InprocChannel()
    ch.bind_loop(asyncio.get_running_loop())
    sid = "s"
    for i in range(5):
        ch.publish_sync(sid, {"kind": "x", "i": i})
    seen: list[int] = []

    async def consume_block() -> None:
        async for frame in ch.subscribe(sid, from_offset=0, overflow="block", bound=2):
            seen.append(frame.offset)
            if frame.offset >= 4:
                break

    await asyncio.wait_for(consume_block(), timeout=2)
    assert seen == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_v2_lifecycle_once() -> None:
    reset_counters()
    ch = InprocChannel()
    set_channel(ch)
    key = RunKey("e", "s", "item1", "r1", attempt=1)
    begin_run(key)
    attempt_reset(key)
    terminal_run(key, "ok")
    kinds = [f.kind for f in ch.replay(key.stream_id())]
    assert kinds.count("run.started") == 1
    assert kinds.count("run.attempt_reset") == 1
    assert kinds.count("run.terminal") == 1
    assert counters().lifecycle["run.started"] == 1
