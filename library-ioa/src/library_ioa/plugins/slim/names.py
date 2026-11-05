#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agency id ↔ four-part SLIM name. The layer builds the name (``<identity>/<agent_id>``); this reads it."""

from __future__ import annotations

import uuid

_INSTANCE_NS = uuid.UUID("3d1c0a5e-6c1f-5b2a-9e4d-7a8b2c1d0e9f")


def instance_uuid(agent_id: str) -> str:
    """SLIM 2.2 instance id: UUID string. Agency ids are mapped with uuid5."""
    value = str(agent_id or "").strip()
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return str(uuid.uuid5(_INSTANCE_NS, value))


def slim_app_triple(name: str) -> tuple[str, str, str]:
    """Three-part app name on a real node.

    A received frame's ``source_name`` is this triple, not the instance
    UUID, so two agents that share ``org/ns/svc`` cannot reply to each
    other. The agency id goes in the third component.
    """
    parts = [p for p in str(name or "").split("/") if p]
    if len(parts) >= 4:
        return parts[0], parts[1], f"{parts[2]}.{parts[3]}"
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    padded = (["mas", "lab", "app"] + parts)[-3:]
    return padded[0], padded[1], padded[2]


def local_id(name: str, agent_id: str = "") -> str:
    """MAS instance key for a SLIM name. ``agent_id`` wins when set."""
    declared = str(agent_id or "").strip().strip("/")
    if declared:
        return declared
    parts = [p for p in str(name or "").split("/") if p]
    if len(parts) >= 4:
        return parts[3]
    return parts[-1] if parts else ""
