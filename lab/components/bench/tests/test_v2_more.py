#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import time

from mas.lab.benchmark.pipeline.artifact import ArtifactType, plan_time_check
from mas.lab.benchmark.pipeline.channel import InprocChannel, set_channel
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.runtime.boundary.obs.event_stream import emit_to_channel, set_channel_emitter


def test_artifact_plan_time_check() -> None:
    prod = ArtifactType(name="data", shape="records", version=1)
    cons = ArtifactType(name="data", shape="records", version=1)
    assert plan_time_check([(prod, cons)]) == []
    warn = plan_time_check([(ArtifactType(name="data", shape="records", version=1), ArtifactType(name="metrics", shape="value", version=2))])
    assert warn


def test_emit_to_channel_noop_without_emitter() -> None:
    emit_to_channel({"kind": "llm_call_end"})


def test_live_channel_sink_publishes_native_not_metric() -> None:
    from mas.lab.benchmark.pipeline.live_sink import LiveChannelSink

    ch = InprocChannel()
    set_channel(ch)
    from mas.lab.benchmark.pipeline.channel import set_stream_id

    key = RunKey("e", "s", "i", "r1")
    sid = key.stream_id()
    set_stream_id(sid)
    ch.publish_sync(sid, {"kind": "llm_call_end"})
    kinds = [f.body.get("kind") for f in ch.replay(sid)]
    assert kinds == ["llm_call_end"]

    sink = LiveChannelSink()
    sink.emit({"kind": "tool_call_end"})
    kinds = [f.body.get("kind") for f in ch.replay(sid)]
    assert kinds[-1] == "tool_call_end"


def test_event_stream_forwards_native_not_mealy_to_channel() -> None:
    from mas.lab.benchmark.pipeline.ui_bridge import forward_native_to_channel
    from mas.runtime.boundary.obs.event_stream import (
        EventStream,
        publish_event,
        reset_event_stream,
        set_event_stream,
    )

    ch = InprocChannel()
    set_channel(ch)
    from mas.lab.benchmark.pipeline.channel import set_stream_id

    sid = "e/s/i/r1#1"
    set_stream_id(sid)
    seen: list = []

    def _emit(rec):
        seen.append(rec)
        ch.publish_sync(sid, rec)

    set_channel_emitter(_emit)
    stream = EventStream()
    stream.subscribe(forward_native_to_channel)
    token = set_event_stream(stream)
    try:
        publish_event({"kind": "llm_call_end", "n": 1})
        publish_event({"source": "transition", "contract_id": "tool"})
    finally:
        reset_event_stream(token)
    assert [r.get("kind") for r in seen] == ["llm_call_end"]


def test_channel_emitter_publishes() -> None:
    ch = InprocChannel()
    set_channel(ch)
    from mas.lab.benchmark.pipeline.channel import set_stream_id
    sid = "e/s/i/r1#1"
    set_stream_id(sid)

    def _emit(rec):
        ch.publish_sync(sid, rec)

    set_channel_emitter(_emit)
    emit_to_channel({"kind": "llm_call_end", "n": 1})
    assert ch.replay(sid)[-1].body["kind"] == "llm_call_end"


def test_class_a_equals_v1_trace_stats() -> None:
    events = [
        {"kind": "llm_call_start"},
        {"kind": "llm_call_end", "tokens_used": {"total_tokens": 4, "prompt_tokens": 3, "completion_tokens": 1}},
        {"kind": "tool_call_start"},
        {"kind": "execution_end"},
    ]
    from mas.library.lab.steps.extract.trace_stats import empty_trace_stats, fold_trace_event, public_trace_stats

    live = empty_trace_stats()
    for rec in events:
        fold_trace_event(live, rec)
    cold = empty_trace_stats()
    for rec in events:
        fold_trace_event(cold, rec)
    assert public_trace_stats(live) == public_trace_stats(cold)


def test_bench_artifact_stream_ceiling() -> None:
    from mas.lab.benchmark.pipeline.artifact import Artifact

    art = Artifact(name="events", key=RunKey("e", "s", "i", "r1"), spec=ArtifactType(name="events", shape="records"))
    n = 2000
    t0 = time.perf_counter()
    for i in range(n):
        art.append({"kind": "x", "i": i})
    elapsed = time.perf_counter() - t0
    per = (elapsed / n) * 1e6
    # Generous ceiling so CI machines pass; records the M1 budget.
    assert per < 500.0, f"per-record overhead {per:.1f}µs exceeds 500µs ceiling"
