#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A native plugin always joins the process bus; emit is a no-op until config is on."""

from __future__ import annotations

from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.schema.transport import MESSAGE_SENT, SOURCE_HOOK, TransportEvent
from mas.runtime.transport import emit
from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
from mas.runtime.transport.emit import emit_transport


def _sinks() -> list:
    return list(emit._fanout._sinks)  # noqa: SLF001


def test_plugin_joins_the_bus_and_close_leaves() -> None:
    emit.reset()
    plugin = NativeObservabilityPlugin(transforms=[], emitters=[])
    try:
        assert _sinks() == [plugin]
    finally:
        plugin.close()
    assert _sinks() == []
    emit.reset()


def test_emit_writes_a_native_record_and_from_binding_reads_the_path(tmp_path) -> None:
    import json
    from types import SimpleNamespace

    from mas.library.standard.lib.observability.emit import JsonlFileEmitter
    from mas.runtime.boundary.obs.binding import ObservabilityBinding

    path = tmp_path / "events.jsonl"
    emit.reset()
    plugin = NativeObservabilityPlugin(transforms=[], emitters=[JsonlFileEmitter(path)])
    try:
        emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
        event = TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=0.0,
        )
        plugin.emit(event)
        plugin.categories_exclude = ("transport.message.sent",)
        plugin.emit(event)
        plugin.categories_exclude = ()
        plugin.emit(SimpleNamespace(to_record=lambda: (_ for _ in ()).throw(RuntimeError("no"))))
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert records and records[0]["kind"] == MESSAGE_SENT
    finally:
        plugin.close()
        emit.reset()

    binding = ObservabilityBinding(plugins=["native"], plugin_configs={"native": {"path": "custom.jsonl"}})
    built = NativeObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="worker")
    try:
        assert built.events_jsonl_path() == (tmp_path / "custom.jsonl").resolve()
    finally:
        built.close()
        emit.reset()


def test_emit_is_silent_until_config_is_on() -> None:
    """Mutation: bind_sink is gated on is_enabled(), so a plugin built before bind_config never sees events."""
    emit.reset()
    captured: list[TransportEvent] = []

    class Spy(NativeObservabilityPlugin):
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    plugin = Spy(transforms=[], emitters=[])
    try:
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
        emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
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
        assert [e.kind for e in captured] == [MESSAGE_SENT]
    finally:
        plugin.close()
        emit.reset()
