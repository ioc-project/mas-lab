#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.ingest import NativeIngest
from mas.lab.controller.ui_stream.inspect import extra_native_kinds, infer_inspect
from mas.lab.controller.ui_stream.metrics import MetricsStore
from mas.lab.controller.ui_stream.refs import DataSourceRef
from mas.lab.controller.ui_stream.replay import KEEP_KINDS, demo_records


def test_infer_inspect_from_plugin_topics() -> None:
    assert infer_inspect(["native.*", "metric.*"]) == []
    assert infer_inspect(["governance.context"]) == ["context"]
    assert infer_inspect(["governance.*"]) == ["context"]
    assert infer_inspect(["*"]) == []
    assert infer_inspect(["inspect.context"]) == ["context"]


def test_replay_omits_context_assembled_until_subscribed() -> None:
    rows = [
        {"kind": "execution_start", "input": "hi"},
        {"kind": "context_assembled", "total_tokens": 9, "messages": [{"content": "secret"}]},
        {"kind": "mas_call_end"},
    ]
    kinds = [item["kind"] for item in demo_records(rows)]
    assert "context_assembled" not in kinds
    assert "execution_start" in kinds
    assert "context_assembled" not in KEEP_KINDS
    extra = extra_native_kinds(["context"])
    compact = list(demo_records(rows, extra_kinds=extra))
    assert any(item["kind"] == "context_assembled" for item in compact)
    assembled = next(item for item in compact if item["kind"] == "context_assembled")
    assert "messages" not in assembled
    assert assembled["total_tokens"] == 9


def test_bus_star_is_not_inspect_demand() -> None:
    bus = EventBus()
    ref = DataSourceRef(library="demo-lib", scenario="baseline")
    bus.subscribe(["*"], DataSourceRef())
    assert bus.wants_topic("governance.context", ref) is True
    assert bus.demands_topic("governance.context", ref) is False
    bus.subscribe(["native.*", "governance.context"], DataSourceRef())
    assert bus.demands_topic("governance.context", ref) is True
    assert bus.demands_topic("governance.queue", ref) is False


def test_ingest_context_assembled_silent_without_subscription() -> None:
    bus = EventBus()
    ingest = NativeIngest(bus, MetricsStore())
    ref = DataSourceRef(session="s1")
    ingest.ingest(
        ref,
        {
            "kind": "context_assembled",
            "total_tokens": 1001,
            "messages": [{"role": "system", "content": "secret prompt"}],
        },
    )
    assert not bus.snapshot(("governance.context",), ref)
    assert not bus.snapshot(("native.context_assembled",), ref)


def test_ingest_star_subscription_does_not_pull_context() -> None:
    bus = EventBus()
    ingest = NativeIngest(bus, MetricsStore())
    ref = DataSourceRef(session="s1")
    bus.subscribe(["*"], ref)
    ingest.ingest(
        ref,
        {
            "kind": "context_assembled",
            "total_tokens": 1001,
            "messages": [{"role": "system", "content": "secret prompt"}],
        },
    )
    assert not bus.snapshot(("governance.context",), ref)
    assert not bus.snapshot(("native.context_assembled",), ref)


def test_ingest_context_assembled_publishes_when_subscribed() -> None:
    bus = EventBus()
    ingest = NativeIngest(bus, MetricsStore())
    ref = DataSourceRef(session="s1")
    bus.subscribe(["governance.context"], ref)
    ingest.ingest(
        ref,
        {
            "kind": "context_assembled",
            "agent_id": "coordinator",
            "total_tokens": 1001,
            "segments": 2,
            "message_count": 2,
            "messages": [{"role": "system", "content": "secret prompt"}],
        },
    )
    native = next(item for item in bus.snapshot(("native.context_assembled",), ref))
    assert "messages" not in native.payload
    gov = [item for item in bus.snapshot(("governance.context",), ref)]
    assert gov
    assert gov[-1].payload["context_tokens"] == 1001
    assert gov[-1].payload["segments"] == 2
    assert "messages" not in gov[-1].payload
