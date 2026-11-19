#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from mas.library.telemetry.transport.projector import project
from mas.runtime.schema.transport import DISCOVERY, MESSAGE_SENT, SOURCE_HOOK, TransportEvent
from mas.runtime.transport.classifier import Alias as AliasSpec
from mas.runtime.transport.classifier import AliasTable


def _table() -> AliasTable:
    return AliasTable(
        events={
            MESSAGE_SENT: AliasSpec(
                event="x.message.sent", attributes={"source": "source.agent", "target": "target.agent"}
            ),
            DISCOVERY: AliasSpec(event="x.card", attributes={"card": "capability.content"}),
        }
    )


def _sent() -> TransportEvent:
    return TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="k1",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={"source": "a", "target": "b"},
    )


def test_none_emits_no_alias() -> None:
    assert project([_sent()], mode="none", aliases=_table(), emit_aliases=True).aliases == []


def test_aliases_off_emits_none() -> None:
    assert project([_sent()], mode="contract", aliases=_table(), emit_aliases=False).aliases == []


def test_contract_maps_the_attributes_the_table_names() -> None:
    (alias,) = project([_sent()], mode="contract", aliases=_table(), emit_aliases=True).aliases
    assert alias.name == "x.message.sent"
    assert alias.attributes["source.agent"] == "a"
    assert alias.attributes["target.agent"] == "b"


def test_mapping_values_become_json_text() -> None:
    """norm reads ``capability.content`` as JSON text. A dict there declares nothing."""
    card = TransportEvent(
        kind=DISCOVERY,
        protocol="example",
        side="client",
        correlation_key="card:peer",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={"card": {"name": "peer"}},
    )
    (alias,) = project([card], mode="contract", aliases=_table(), emit_aliases=True).aliases
    assert isinstance(alias.attributes["capability.content"], str)
    assert json.loads(alias.attributes["capability.content"]) == {"name": "peer"}


def test_projection_reads_one_event_at_a_time() -> None:
    """The stream projects the same as each event alone: no state kept between events."""
    events = [_sent(), _sent()]
    together = project(events, mode="contract", aliases=_table(), emit_aliases=True).aliases
    alone = [a for e in events for a in project([e], mode="contract", aliases=_table(), emit_aliases=True).aliases]
    assert together == alone


def test_unserializable_value_falls_back_to_str(monkeypatch) -> None:
    from mas.library.telemetry.transport import projector as projector_mod

    def boom(*_args, **_kwargs):
        raise TypeError("no")

    monkeypatch.setattr(projector_mod.json, "dumps", boom)
    card = TransportEvent(
        kind=DISCOVERY,
        protocol="example",
        side="client",
        correlation_key="card:peer",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={"card": {"name": "peer"}},
    )
    (alias,) = project([card], mode="contract", aliases=_table(), emit_aliases=True).aliases
    assert alias.attributes["capability.content"] == str({"name": "peer"})
    none = TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="k",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={},
    )
    assert project([none], mode="contract", aliases=_table(), emit_aliases=True).aliases


def test_event_source_hook_is_not_mapped_as_an_agent() -> None:
    """Mutation: hasattr(event, native_key) copies TransportEvent.source ('hook') onto the alias."""
    event = TransportEvent(
        kind=MESSAGE_SENT,
        protocol="example",
        side="client",
        correlation_key="k1",
        source=SOURCE_HOOK,
        timestamp=1.0,
        attributes={"target": "b"},
    )
    (alias,) = project([event], mode="contract", aliases=_table(), emit_aliases=True).aliases
    assert "source.agent" not in alias.attributes
    assert "hook" not in alias.attributes.values()
    assert alias.attributes["target.agent"] == "b"
