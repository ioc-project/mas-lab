#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CommBus protocol — mas-ctl wires RuntimeInstances; kernel emits TRANSPORT_MSG."""

from __future__ import annotations

from typing import Protocol

from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class CommEndpoint(Protocol):
    """One agent endpoint on the bus (registered by mas-ctl)."""

    agent_id: str

    async def adeliver(self, payload: EngineIoReturn) -> None:
        """Deliver inbound inter-agent message as ENGINE_IO_RETURN."""


class CommBus(Protocol):
    """Inter-agent transport — implementation selected by flavour/placement plan."""

    def register(self, agent_id: str, endpoint: CommEndpoint) -> None:
        """Register a runtime instance endpoint."""

    async def asend(self, *, from_agent: str, to_agent: str, intent: InvokeEngineIo) -> EngineIoReturn | None:
        """Route TRANSPORT_MSG; returns synthetic ack for the local bus."""

    def resolve_address(self, agent_id: str) -> str:
        """Return bind address (inproc://, unix://, host:port) for placement introspection."""
