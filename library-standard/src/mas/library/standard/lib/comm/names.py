#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Hierarchical comm names: non-empty parts, no ``//``, restricted characters."""

from __future__ import annotations

import re

from mas.runtime.boundary.agentcomm.protocol import AgentCommError

_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_name(name: str) -> tuple[str, ...]:
    """Split ``org/ns/...`` into parts, or raise ``AgentCommError``."""
    if not isinstance(name, str) or not name:
        raise AgentCommError(f"empty name {name!r}")
    if name.startswith("/") or name.endswith("/"):
        raise AgentCommError(f"empty name part in {name!r}")
    parts = tuple(name.split("/"))
    for part in parts:
        if not part:
            raise AgentCommError(f"empty name part in {name!r}")
        if not _PART.fullmatch(part):
            raise AgentCommError(f"illegal name part {part!r}")
    return parts


def require_instance_name(name: str) -> tuple[str, str, str, str]:
    """Four-part ``org/ns/service/instance`` identity for a local-group link."""
    parts = parse_name(name)
    if len(parts) != 4:
        raise AgentCommError("local-group name must be org/ns/service/instance")
    return parts[0], parts[1], parts[2], parts[3]


def namespace_of(parts: tuple[str, ...]) -> str:
    """``org/ns`` from a name that has at least two parts."""
    if len(parts) < 2:
        raise AgentCommError(f"name {'/'.join(parts)!r} needs org/ns")
    return f"{parts[0]}/{parts[1]}"
