#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bridge RuntimeInstance to CommBus endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class RuntimeCommEndpoint:
    """Wraps RuntimeInstance so mas-ctl can register agents on InProcessCommBus."""

    agent_id: str
    instance: RuntimeInstance

    async def adeliver(self, payload: EngineIoReturn) -> None:
        await self.instance.afeed(payload)
