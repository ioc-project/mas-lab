#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Project native transport events to the observe-sdk compatibility events SCOPE's norm reads.

This is the only projection the otel sink exports today: one alias event per
native event, built from that event alone. Span nesting (a contract span that
parents a converted SDK span) is not exported yet, so it is not offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from mas.runtime.schema.transport import TransportEvent
from mas.runtime.transport.classifier import AliasTable


@dataclass(frozen=True)
class AliasEvent:
    """observe-sdk-shaped compatibility event."""

    name: str
    attributes: Mapping[str, Any]


@dataclass
class ProjectResult:
    aliases: list[AliasEvent] = field(default_factory=list)


def _attribute_value(value: Any) -> Any:
    """OTel attribute values are scalars or sequences of scalars. A mapping or nested list becomes JSON text.

    norm reads ``capability.content`` as JSON text; a dict there declares nothing.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _alias_event(event: TransportEvent, table: AliasTable) -> AliasEvent | None:
    alias = table.events.get(event.kind)
    if alias is None:
        return None
    attrs: dict[str, Any] = {}
    raw = dict(event.attributes)
    for native_key, exported in alias.attributes.items():
        if native_key not in raw:
            continue
        attrs[exported] = _attribute_value(raw[native_key])
    if event.agent_id and "agent.name" not in attrs:
        attrs.setdefault("agent.name", event.agent_id)
    if event.mas_id:
        attrs.setdefault("app.name", event.mas_id)
    return AliasEvent(name=alias.event, attributes=attrs)


def project(
    events: Sequence[TransportEvent] | Iterable[TransportEvent],
    *,
    mode: str = "contract",
    aliases: AliasTable | None = None,
    emit_aliases: bool = False,
) -> ProjectResult:
    """Alias events for a stream of native events. ``mode=none`` or ``emit_aliases=False`` gives none."""
    if mode == "none" or not emit_aliases:
        return ProjectResult()
    table = aliases or AliasTable()
    out = [aliased for aliased in (_alias_event(event, table) for event in events) if aliased is not None]
    return ProjectResult(aliases=out)


__all__ = ["AliasEvent", "ProjectResult", "project"]
