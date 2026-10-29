# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""dispatch_steer rejects an unknown mode before calling steer."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.lib.comm.payload import dispatch_steer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.boundary.control.contract import ControlCapability


class _SteerHandle:
    def __init__(self) -> None:
        self.capability = ControlCapability(
            actor="peer",
            surface="peer",
            session_ids=frozenset({"s1"}),
            methods=frozenset({"steer"}),
        )
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def steer(self, session_id: str, **kwargs: Any) -> None:
        self.seen.append((session_id, kwargs))


def test_dispatch_steer_rejects_unknown_mode() -> None:
    handle = _SteerHandle()
    with pytest.raises(AgentCommError, match="unknown steer mode"):
        dispatch_steer(
            handle,
            session_id="s1",
            text="go via Lyon",
            mode="explode",
            target="s1",
            allowed=["s1"],
            sender="planner",
        )
    assert handle.seen == []
