#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import builtins
import io
import json
import threading
from pathlib import Path

import pytest
from mas.lab.benchmark.pipeline.channel import (
    InprocChannel,
    LogChannel,
    get_channel,
    install_channel,
    make_channel,
    reset_channel,
    set_channel,
)
from mas.lab.benchmark.pipeline.counters import counters, reset_counters
from mas.lab.benchmark.pipeline.digest import rolling_digest
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.lifecycle import attempt_reset, begin_run, terminal_run


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


def test_log_channel_bytes_match_frame_dumps(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    bodies = [{"kind": "llm_call_end", "n": i} for i in range(4)]
    for body in bodies:
        ch.publish_sync("s", body)
    frames = ch.replay("s")
    expected = "".join(json.dumps(frame.as_dict(), ensure_ascii=False, default=str) + "\n" for frame in frames)
    assert ch._path("s").read_text(encoding="utf-8") == expected
    ch.seal_sync("s")


def test_log_channel_reader_sees_each_line_after_publish(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    path = ch._path("s")
    for n in range(5):
        ch.publish_sync("s", {"kind": "x", "n": n})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n + 1
        assert json.loads(lines[-1])["body"]["n"] == n
    ch.seal_sync("s")


def test_log_channel_opens_once_per_stream_and_closes_on_seal(tmp_path) -> None:
    real_open = io.open
    calls: list[tuple[str, str]] = []

    def counting_open(file, mode="r", *args, **kwargs):  # noqa: ANN001
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            calls.append((str(file), str(mode)))
        return real_open(file, mode, *args, **kwargs)

    io.open = counting_open  # type: ignore[assignment]
    builtins.open = counting_open  # type: ignore[assignment]
    try:
        ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
        for n in range(20):
            ch.publish_sync("s", {"kind": "x", "n": n})
        jsonl_opens = [path for path, _mode in calls if Path(path).suffix == ".jsonl"]
        assert len(jsonl_opens) == 1
        handle = ch._files["s"].handle
        assert not handle.closed
        ch.seal_sync("s")
        assert handle.closed
        assert "s" not in ch._files
    finally:
        io.open = real_open  # type: ignore[assignment]
        builtins.open = real_open  # type: ignore[assignment]


def test_log_channel_close_closes_unsealed_handle(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    ch.publish_sync("s", {"kind": "x", "n": 1})
    handle = ch._files["s"].handle
    assert not handle.closed
    ch.close()
    assert handle.closed
    assert not ch._files


def test_log_channel_publish_after_close_raises(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    ch.publish_sync("s", {"kind": "x", "n": 1})
    path = ch._path("s")
    before = path.read_text(encoding="utf-8")
    ch.close()
    with pytest.raises(RuntimeError, match="after close"):
        ch.publish_sync("s", {"kind": "y", "n": 2})
    assert path.read_text(encoding="utf-8") == before
    assert ch.inner.replay("s")[-1].body["kind"] == "x"


def test_make_channel_publish_after_close_raises(tmp_path) -> None:
    ch = make_channel(log_root=tmp_path / "streams")
    ch.publish_sync("s", {"kind": "x"})
    ch.close()
    with pytest.raises(RuntimeError, match="after close"):
        ch.publish_sync("s", {"kind": "y"})


def test_log_channel_concurrent_streams_do_not_interleave(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    payload = "abcdefghij" * 20

    def worker(stream_id: str) -> None:
        for n in range(40):
            ch.publish_sync(stream_id, {"kind": "x", "n": n, "sid": stream_id, "payload": payload})

    threads = [threading.Thread(target=worker, args=(sid,)) for sid in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for sid in ("a", "b"):
        loaded = ch.load_file(sid)
        assert [frame.body["n"] for frame in loaded] == list(range(40))
        assert all(frame.body["payload"] == payload for frame in loaded)
        assert all(frame.body["sid"] == sid for frame in loaded)
        ch.seal_sync(sid)


def test_log_channel_encoded_line_matches_as_dict_dump(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    frame = ch.publish_frame_sync("s", {"kind": "llm_call_end", "n": 1})
    assert frame.encoded == json.dumps(frame.as_dict(), ensure_ascii=False, default=str) + "\n"
    assert ch._path("s").read_text(encoding="utf-8") == frame.encoded
    ch.seal_sync("s")


def test_json_dumps_per_event_at_most_four(tmp_path, monkeypatch) -> None:
    import json as json_mod

    ch = make_channel(log_root=tmp_path / "streams")
    counted = {"n": 0}
    real = json_mod.dumps

    def wrapped(obj, *args, **kwargs):  # noqa: ANN001
        counted["n"] += 1
        return real(obj, *args, **kwargs)

    monkeypatch.setattr("mas.lab.benchmark.pipeline.channel.json.dumps", wrapped)
    monkeypatch.setattr("mas.lab.benchmark.pipeline.digest.json.dumps", wrapped)
    n_events = 10
    for i in range(n_events):
        ch.publish_frame_sync("s", {"kind": "llm_call_end", "n": i})
    per_event = counted["n"] / n_events
    # digest + one encoded line; the log writes that line and does not dump again.
    assert per_event <= 2, f"expected at most 2 json.dumps per event, got {per_event}"
    ch.seal_sync("s")


def test_log_tail_returns_each_line_once(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    for n in range(6):
        ch.publish_sync("s", {"kind": "x", "n": n})
    lines = ch._path("s").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    assert [json.loads(line)["body"]["n"] for line in lines] == list(range(6))
    ch.seal_sync("s")


def test_log_channel_concurrent_writes_on_one_stream_stay_whole_lines(tmp_path) -> None:
    ch = LogChannel(tmp_path / "streams", inner=InprocChannel())
    payload = "abcdefghij" * 20

    def worker(tag: str) -> None:
        for n in range(40):
            ch.publish_sync("s", {"kind": "x", "tag": tag, "n": n, "payload": payload})

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = ch._path("s").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 80
    assert {row["body"]["tag"] for row in parsed} == {"a", "b"}
    ch.seal_sync("s")


def test_install_channel_cache_root_wins_over_existing(tmp_path: Path) -> None:
    previous = get_channel()
    try:
        first = make_channel(log_root=tmp_path / "first")
        set_channel(first)
        first.publish_sync("old", {"kind": "x"})
        second = install_channel(cache_root=tmp_path / "cache")
        assert second is not first
        second.publish_sync("s", {"kind": "run.started"})
        lines = (tmp_path / "cache" / "streams" / "s.jsonl").read_text(encoding="utf-8")
        assert "run.started" in lines
    finally:
        set_channel(previous)


def test_reset_channel_does_not_leave_the_first_channel(tmp_path: Path) -> None:
    previous = get_channel()
    try:
        first = make_channel(log_root=tmp_path / "a")
        token = set_channel(first)
        second = make_channel(log_root=tmp_path / "b")
        nested = set_channel(second)
        assert get_channel() is second
        reset_channel(nested)
        assert get_channel() is first
        reset_channel(token)
        assert get_channel() is not first
        assert get_channel() is not second
        seen: list[object] = []

        def worker() -> None:
            seen.append(get_channel())

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert seen == [get_channel()]
        assert seen[0] is not first
        assert seen[0] is not second
    finally:
        set_channel(previous)
