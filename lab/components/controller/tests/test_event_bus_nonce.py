#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.refs import DataSourceRef, envelope, use_nonce, reset_nonce


def test_untagged_envelopes_reach_every_subscriber() -> None:
    bus = EventBus(max_history=50)
    a = bus.subscribe(["job.*"], DataSourceRef(), nonce="aaa")
    b = bus.subscribe(["job.*"], DataSourceRef(), nonce="bbb")
    bus.publish(envelope("job.started", DataSourceRef(), "job", {"ok": True}))

    async def take(sub):
        return await asyncio.wait_for(sub.queue.get(), timeout=0.2)

    first = asyncio.run(take(a))
    second = asyncio.run(take(b))
    assert first.topic == "job.started"
    assert second.topic == "job.started"
    assert first.nonce is None


def test_tagged_envelopes_stay_on_one_channel() -> None:
    bus = EventBus(max_history=50)
    a = bus.subscribe(["native.*"], DataSourceRef(), nonce="aaa")
    b = bus.subscribe(["native.*"], DataSourceRef(), nonce="bbb")
    token = use_nonce("aaa")
    try:
        bus.publish(envelope("native.llm_call_end", DataSourceRef(), "native", {"kind": "llm_call_end"}))
    finally:
        reset_nonce(token)

    async def take(sub):
        return await asyncio.wait_for(sub.queue.get(), timeout=0.2)

    hit = asyncio.run(take(a))
    assert hit.nonce == "aaa"
    try:
        asyncio.run(take(b))
        raise AssertionError("other channel must not see tagged telemetry")
    except asyncio.TimeoutError:
        pass


def test_replay_nonce_reaches_every_subscriber() -> None:
    bus = EventBus(max_history=50)
    a = bus.subscribe(["custom.*", "view.reset"], DataSourceRef(), nonce="ui-tab")
    token = use_nonce("replay-1")
    try:
        bus.publish(
            envelope(
                "view.reset",
                DataSourceRef(),
                "view",
                {"phase": "reset"},
                replay=True,
            )
        )
        bus.publish(
            envelope(
                "custom.chat.snapshot",
                DataSourceRef(),
                "custom",
                {"cards": [{"role": "user", "depth": 0}]},
                replay=True,
            )
        )
    finally:
        reset_nonce(token)

    async def take(sub):
        return await asyncio.wait_for(sub.queue.get(), timeout=0.2)

    reset = asyncio.run(take(a))
    chat = asyncio.run(take(a))
    assert reset.topic == "view.reset"
    assert chat.topic == "custom.chat.snapshot"
    assert chat.nonce == "replay-1"


def test_catchup_sends_latest_artefact_after_reset() -> None:
    bus = EventBus(max_history=50)
    ref = DataSourceRef()
    bus.publish(envelope("custom.chat.snapshot", ref, "custom", {"cards": [{"text": "old"}]}))
    bus.publish(envelope("view.reset", ref, "view", {"phase": "reset"}, replay=True))
    bus.publish(envelope("spec.snapshot", ref, "spec", {"agents": [{"id": "entry"}]}))
    bus.publish(
        envelope(
            "custom.chat.snapshot",
            ref,
            "custom",
            {"cards": [{"text": "new"}]},
            replay=True,
        )
    )
    bus.publish(
        envelope(
            "custom.chat.snapshot",
            ref,
            "custom",
            {"cards": [{"text": "newer"}]},
            replay=True,
        )
    )
    rows = bus.catchup(
        ["spec.snapshot", "spec.*", "custom.chat", "custom.chat.*", "view.reset"],
        ref,
    )
    topics = [item.topic for item in rows]
    assert topics[0] == "view.reset"
    chat = next(item for item in rows if item.topic == "custom.chat.snapshot")
    assert chat.payload["cards"][0]["text"] == "newer"
    assert any(item.topic == "spec.snapshot" for item in rows)
    assert bus.catchup([], ref) == []
