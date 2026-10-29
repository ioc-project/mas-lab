#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared steer and capability bits for in-process local links."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from mas.library.standard.lib.comm.capabilities import capability_set, read_capabilities
from mas.library.standard.lib.comm.payload import INFORM_ASK_INTENTS, STEER_INTENT, steer_peer_sessions
from mas.runtime.boundary.control.contract import ControlContract


class LocalLinkBase:
    """Steer sessions, honoured intents, and declared capability flags."""

    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(unicast=True)
    HONOURED_SEND_INTENTS = INFORM_ASK_INTENTS

    def __init__(
        self,
        *,
        agent_id: str = "",
        control: ControlContract | None = None,
        steer_peers: Mapping[str, str] | Sequence[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._control = control
        self._steer_sessions = steer_peer_sessions(steer_peers)

    def capabilities(self) -> dict[str, bool]:
        return read_capabilities(type(self)) or capability_set()

    def honoured_send_intents(self) -> frozenset[str]:
        if self._control is None:
            return self.HONOURED_SEND_INTENTS
        return self.HONOURED_SEND_INTENTS | {STEER_INTENT}
