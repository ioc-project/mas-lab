#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process CommBus — local-monolith flavour (N runtimes, one process)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.ctl.placement.bus.protocol import CommEndpoint
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class InProcessCommBus:
    """Zero-copy local routing between RuntimeInstance endpoints.

    Safe for concurrent turns because ``asend`` reads the endpoint table
    before it awaits, and registration happens before concurrency starts.
    The delivery is awaited: a sender runs on the event loop, and a
    synchronous delivery would wait for async work inside the running loop.
    """

    _endpoints: dict[str, CommEndpoint] = field(default_factory=dict)

    def register(self, agent_id: str, endpoint: CommEndpoint) -> None:
        self._endpoints[agent_id] = endpoint

    def unregister(self, agent_id: str) -> None:
        """Remove one dynamically materialized endpoint after its turn ends."""
        self._endpoints.pop(agent_id, None)

    def resolve_address(self, agent_id: str) -> str:
        return f"inproc://{agent_id}"

    async def asend(self, *, from_agent: str, to_agent: str, intent: InvokeEngineIo) -> EngineIoReturn | None:
        target = self._endpoints.get(to_agent)
        if target is None:
            return EngineIoReturn(
                correlation_id=intent.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
            )
        result = EngineIoReturn(
            correlation_id=intent.correlation_id,
            response_kind="TRANSPORT_ACK",
            next_step="STOP",
        )
        await target.adeliver(result)
        return result
