#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Transport observability invariants. Each docstring names the mutation that fails it."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from library_ioa.plugins.a2a.classifier import A2ATransportClassifier
from mas.library.kg.pipeline import build_kg_document
from mas.library.telemetry.transport.projector import project
from mas.runtime.schema.transport import (
    INTERNAL,
    MESSAGE_SENT,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.classifier import Drop, Emit, SpanView


def _sent() -> TransportEvent:
    return TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="call-1",
        source=SOURCE_HOOK,
        timestamp=1.0,
        mas_id="demo",
        agent_id="caller",
        attributes={"source": "caller", "target": "peer"},
    )


def _sdk_message_span() -> SpanView:
    return SpanView(
        name="a2a.client.transports.jsonrpc.JsonRpcTransport.send_message",
        scope_name="a2a-python-sdk",
        kind="CLIENT",
        span_id="1",
    )


def _graph(events: list[dict]):
    doc = build_kg_document(events, run_id="r1")
    nodes = sorted(doc["nodes"], key=lambda n: (n.get("node_type"), n.get("id")))
    edges = sorted(doc["edges"], key=lambda e: (e.get("edge_type"), e.get("from_id"), e.get("to_id")))
    return nodes, edges


class _FakeSpan:
    """Just enough of an SDK span for the intake processor."""

    def __init__(self, name: str, *, scope: str = "a2a-python-sdk", kind: str = "CLIENT", span_id: int = 1) -> None:
        from types import SimpleNamespace

        self.name = name
        self.parent = None
        self.attributes: dict = {}
        self.status = SimpleNamespace(status_code=None)
        self.kind = SimpleNamespace(name=kind)
        self.start_time = 1_000_000_000
        self.end_time = 2_000_000_000
        self.instrumentation_scope = SimpleNamespace(name=scope, version="1")
        self._ctx = SimpleNamespace(span_id=span_id, trace_id=7)

    def get_span_context(self):
        return self._ctx


def _run_call_through_intake(span_names: list[str]) -> list[TransportEvent]:
    """One hook event plus the SDK spans of the same call, through the real processor and bus."""
    from threading import Lock

    from library_ioa.plugins.a2a import hooks
    from mas.library.telemetry.transport.intake import IntakeConfig, _Processor
    from mas.library.telemetry.transport.registry import ClassifierSet
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(internals=True, sdk_spans=True),
        )
    )
    emit.bind_sink(Sink())
    try:
        processor = _Processor(
            ClassifierSet(classifiers=(A2ATransportClassifier(),)),
            IntakeConfig(),
            {},
            Lock(),
        )
        hooks.emit_sent(key="c1|t1|m1", source="caller", target="peer")
        for index, name in enumerate(span_names, start=1):
            span = _FakeSpan(name, span_id=index)
            processor.on_start(span)
            processor.on_end(span)
    finally:
        emit.reset()
    return captured


def test_invariant_1_one_call_one_message_event() -> None:
    """Mutation: classify_end returns Emit(kind=MESSAGE_SENT) for send_message spans."""
    events = _run_call_through_intake(
        [
            "a2a.client.transports.jsonrpc.JsonRpcTransport.send_message",
            "a2a.server.request_handlers.default_request_handler_v2.DefaultRequestHandlerV2.on_message_send",
            "a2a.server.events.event_queue_v2.EventQueueSource.enqueue_event",
        ]
    )
    sent = [e for e in events if e.kind == MESSAGE_SENT]
    assert len(sent) == 1
    assert sent[0].source == SOURCE_HOOK


def test_internal_sdk_span_is_announced_once() -> None:
    """Mutation: classify_start returns Passthrough, so a span is announced at start and at end."""
    events = _run_call_through_intake(["a2a.server.events.event_queue_v2.EventQueueSource.enqueue_event"])
    internals = [e for e in events if e.kind == INTERNAL]
    assert len(internals) == 1


def test_invariant_2_sdk_span_is_not_a_message_event() -> None:
    """Mutation: classify_end returns Emit(kind=MESSAGE_SENT) for send_message."""
    decision = A2ATransportClassifier().classify_end(_sdk_message_span())
    assert not isinstance(decision, Emit) or decision.event_fields.get("kind") != MESSAGE_SENT
    assert isinstance(decision, Drop)


def test_invariant_3_projection_is_stateless() -> None:
    """Mutation: the projector keeps a cache between events, so a stream differs from its parts."""
    from mas.runtime.transport.classifier import Alias as AliasSpec
    from mas.runtime.transport.classifier import AliasTable

    table = AliasTable(events={MESSAGE_SENT: AliasSpec(event="x.sent", attributes={"source": "source.agent"})})
    events = [_sent(), _sent()]
    together = project(events, mode="contract", aliases=table, emit_aliases=True).aliases
    alone = [a for e in events for a in project([e], mode="contract", aliases=table, emit_aliases=True).aliases]
    assert together == alone and len(together) == 2


def test_invariant_4_unconfigured_process_emits_nothing() -> None:
    """Mutation: the bus starts enabled, so hooks emit with no transport config."""
    from library_ioa.plugins.a2a import hooks
    from mas.runtime.transport import emit

    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_sink(Sink())
    try:
        hooks.emit_sent(key="k", source="a", target="b")
    finally:
        emit.reset()
    assert captured == []


def test_invariant_5_shuffle_same_graph() -> None:
    """Mutation: handler stores arrival index on the Agent id."""
    sent = _sent().to_record()
    received = dict(sent)
    received["kind"] = "transport.message.received"
    received["timestamp"] = 2.0
    a = _graph([sent, received])
    b = _graph([received, sent])
    assert a == b


def test_invariant_6_duplicate_same_graph() -> None:
    """Mutation: catalog() appends a suffix on each merge."""
    sent = _sent().to_record()
    assert _graph([sent]) == _graph([sent, sent])


def test_invariant_7_mapping_cost_constant() -> None:
    """Mutation: handler walks builder.nodes (grows with run length)."""
    import time

    event = _sent().to_record()
    times: list[float] = []
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder

    builder = NativeGraphBuilder(run_id="r1")
    for i in range(2000):
        ev = dict(event)
        ev["attributes"] = {"source": f"s{i}", "target": f"t{i}"}
        start = time.perf_counter()
        builder.process_event(ev)
        times.append(time.perf_counter() - start)
    early = sum(times[:50]) / 50
    late = sum(times[-50:]) / 50
    assert late <= early * 3 + 1e-4


def test_invariant_8_unknown_span_degrades() -> None:
    """Mutation: Passthrough is dropped instead of Emit(INTERNAL)."""
    from threading import Lock

    from mas.library.telemetry.transport.intake import IntakeConfig, _Processor
    from mas.library.telemetry.transport.registry import ClassifierSet

    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
    from mas.runtime.transport.emit import bind_config, bind_sink, reset

    reset()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(internals=True, sdk_spans=True),
        )
    )
    bind_sink(Sink())
    processor = _Processor(
        ClassifierSet(classifiers=(A2ATransportClassifier(),)),
        IntakeConfig(),
        {},
        Lock(),
    )
    view = SpanView(name="totally.unknown", scope_name="a2a-python-sdk", span_id="zz")
    processor._apply(A2ATransportClassifier().classify_end(view), view, A2ATransportClassifier(), start=False)
    assert captured
    assert captured[0].kind == INTERNAL
    reset()


def test_invariant_9_core_has_no_protocol_names() -> None:
    """Mutation: write 'a2a' into runtime/transport/config.py."""
    root = Path(__file__).resolve().parents[2] / "src" / "mas" / "library" / "telemetry" / "transport"
    runtime = Path(__file__).resolve().parents[3] / "runtime" / "src" / "mas" / "runtime" / "transport"
    for path in list(root.glob("*.py")) + list(runtime.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for name in ("a2a", "mcp", "slim"):
            assert name not in text, path


def test_intake_never_raises_into_the_caller() -> None:
    """Mutation: _handle stops catching, so a bad span or classifier raises inside the SDK's start_span."""
    from threading import Lock

    from mas.library.telemetry.transport.intake import IntakeConfig, _Processor
    from mas.library.telemetry.transport.registry import ClassifierSet

    class Boom(A2ATransportClassifier):
        def classify_end(self, span):  # type: ignore[override]
            return Emit(
                {"kind": INTERNAL, "protocol": "a2a", "timestamp": "not-a-number", "attributes": {"x": object()}}
            )

    processor = _Processor(ClassifierSet(classifiers=(Boom(),)), IntakeConfig(), {}, Lock())
    processor.on_start(object())  # not a span at all
    span = _FakeSpan("a2a.anything")
    processor.on_start(span)
    processor.on_end(span)


def test_r4_intake_never_attaches_context() -> None:
    """Mutation: call context.attach or start_as_current_span in intake._Processor."""
    path = Path(__file__).resolve().parents[2] / "src" / "mas" / "library" / "telemetry" / "transport" / "intake.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"attach", "detach", "start_as_current_span"}


def test_r1_off_means_zero_opentelemetry_import() -> None:
    """Mutation: import opentelemetry at module top of telemetry.transport.warnings."""
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import mas.library.telemetry.transport.warnings as w\n"
        "assert not any(n.startswith('opentelemetry') for n in sys.modules if n not in before)\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_r7_overhead_does_not_grow_faster_than_linear() -> None:
    """Mutation: the processor keeps every span it has seen (a list that is scanned or copied per span)."""
    import statistics
    import time
    import tracemalloc
    from threading import Lock

    from mas.library.telemetry.transport.intake import IntakeConfig, _Processor
    from mas.library.telemetry.transport.registry import ClassifierSet
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    class Discard:
        def emit(self, event: TransportEvent) -> None:
            return None

    def run(n: int) -> tuple[float, int]:
        emit.reset()
        emit.bind_config(
            TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(internals=True, sdk_spans=True))
        )
        emit.bind_sink(Discard())
        processor = _Processor(ClassifierSet(classifiers=(A2ATransportClassifier(),)), IntakeConfig(), {}, Lock())
        spans = [_FakeSpan("a2a.some.internal.step", span_id=i + 1) for i in range(n)]
        tracemalloc.start()
        start = time.perf_counter()
        for span in spans:
            processor.on_start(span)
            processor.on_end(span)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        emit.reset()
        return elapsed / n, peak

    def median_per_span(n: int, *, repeats: int = 5) -> tuple[float, int]:
        run(n)
        samples = [run(n) for _ in range(repeats)]
        return statistics.median(s[0] for s in samples), max(s[1] for s in samples)

    per_1k, peak_1k = median_per_span(1000)
    per_10k, peak_10k = median_per_span(10000)
    print(
        f"transport overhead: 1k={per_1k * 1e6:.1f}us/span peak={peak_1k // 1024}KiB "
        f"10k={per_10k * 1e6:.1f}us/span peak={peak_10k // 1024}KiB"
    )
    assert per_10k <= per_1k * 3 + 1e-6
    assert peak_10k <= peak_1k * 3 + 64 * 1024  # memory is the span copies of one call, not the run so far
