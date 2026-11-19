#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Transport events round-trip; runtime transport code imports no OpenTelemetry."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from mas.runtime.schema.transport import (
    DISCOVERY,
    MESSAGE_SENT,
    SESSION,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.config import DISABLED, ProtocolTransportConfig, TransportObservabilityConfig
from mas.runtime.transport.emit import bind_config, emit_transport, is_enabled, protocol_config, reset

RUNTIME_TRANSPORT = Path(__file__).resolve().parents[1] / "src" / "mas" / "runtime" / "transport"
RUNTIME_SCHEMA = Path(__file__).resolve().parents[1] / "src" / "mas" / "runtime" / "schema" / "transport.py"


def test_event_round_trips_to_jsonl() -> None:
    event = TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="ctx|task|msg",
        source=SOURCE_HOOK,
        timestamp=1.5,
        session_id="s",
        agent_id="caller",
        call_id="c1",
        run_id="r1",
        mas_id="demo",
        attributes={"source": "caller", "target": "peer", "extra": "kept"},
    )
    line = json.dumps(event.to_record())
    restored = TransportEvent.from_record(json.loads(line))
    assert restored == event
    assert restored.attributes["extra"] == "kept"


def test_event_is_mappable_from_own_fields() -> None:
    event = TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="k",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={"source": "a", "target": "b"},
    )
    record = event.to_record()
    assert record["attributes"]["source"] == "a"
    assert record["attributes"]["target"] == "b"
    assert "lookup" not in record


def test_unknown_extra_attributes_are_kept() -> None:
    raw = {
        "kind": MESSAGE_SENT,
        "protocol": "example",
        "side": "server",
        "correlation_key": "k",
        "source": SOURCE_HOOK,
        "timestamp": 0,
        "attributes": {"source": "a", "target": "b", "custom": 3},
        "ignored_top_level": True,
    }
    event = TransportEvent.from_record(raw)
    assert event.attributes["custom"] == 3


def test_off_means_emit_is_noop() -> None:
    reset()
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    from mas.runtime.transport.emit import bind_sink

    bind_sink(Sink())
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    assert captured == []
    assert is_enabled() is False


def test_enabled_config_delivers() -> None:
    reset()
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    from mas.runtime.transport.emit import bind_sink

    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    bind_sink(Sink())
    event = TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="k",
        source=SOURCE_HOOK,
        timestamp=0.0,
        attributes={"source": "a", "target": "b"},
    )
    emit_transport(event)
    assert [e.kind for e in captured] == [MESSAGE_SENT]
    reset()


def _python_files() -> list[Path]:
    files = [RUNTIME_SCHEMA]
    files.extend(sorted(RUNTIME_TRANSPORT.glob("*.py")))
    return files


def _capture() -> list[TransportEvent]:
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    from mas.runtime.transport.emit import bind_sink

    bind_sink(Sink())
    return captured


def test_disabled_protocol_is_fully_off() -> None:
    """Mutation: protocol_config returns hooks=True when enabled is false."""
    reset()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(),
            protocols={"example": ProtocolTransportConfig(enabled=False, hooks=True)},
        )
    )
    assert protocol_config("example") == DISABLED
    captured = _capture()
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    assert captured == []
    reset()


def test_hooks_false_skips_scope_kinds() -> None:
    """Mutation: hooks=False still emits MESSAGE_SENT or DISCOVERY."""
    reset()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(hooks=False),
        )
    )
    captured = _capture()
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    emit_transport(
        TransportEvent(
            kind=DISCOVERY,
            protocol="example",
            side="client",
            correlation_key="card",
            source=SOURCE_HOOK,
            timestamp=0.0,
            attributes={"discovery_kind": "agent_card"},
        )
    )
    assert captured == []
    reset()


def test_internals_false_keeps_discovery_drops_session() -> None:
    """Mutation: internals=False still emits SESSION."""
    reset()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(internals=False),
        )
    )
    captured = _capture()
    emit_transport(
        TransportEvent(
            kind=DISCOVERY,
            protocol="example",
            side="client",
            correlation_key="card",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    emit_transport(
        TransportEvent(
            kind=SESSION,
            protocol="example",
            side="client",
            correlation_key="link",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    assert [e.kind for e in captured] == [DISCOVERY, MESSAGE_SENT]
    reset()


def test_two_equal_sinks_both_receive() -> None:
    """Mutation: FanOut dedupes with ==, so two equal dataclass plugins collapse to one."""
    reset()
    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    captured: list[str] = []

    class Sink:
        def __init__(self, name: str) -> None:
            self.name = name

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Sink)

        def emit(self, event: TransportEvent) -> None:
            captured.append(self.name)

    from mas.runtime.transport.emit import bind_sink

    bind_sink(Sink("a"))
    bind_sink(Sink("b"))
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    assert captured == ["a", "b"]
    reset()


def test_sink_exception_does_not_propagate() -> None:
    """Mutation: a raising sink fails the caller of emit_transport."""
    reset()
    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))

    class Boom:
        def emit(self, event: TransportEvent) -> None:
            raise RuntimeError("sink")

    from mas.runtime.transport.emit import bind_sink, dropped_count

    bind_sink(Boom())
    emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
    )
    assert dropped_count() == 1
    reset()


def test_runtime_transport_imports_no_opentelemetry() -> None:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("opentelemetry"), path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("opentelemetry"), path
