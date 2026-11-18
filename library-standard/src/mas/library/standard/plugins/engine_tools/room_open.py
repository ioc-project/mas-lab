#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine tool: open a shared room on a group channel and return its result."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.engine.tools import OPEN_ROOM_TOOL

# The rule names a manifest may set on the tool; they travel to the members in the ``open`` message.
RULE_KEYS = ("closer", "round_cap", "post_timeout_s", "turn", "agree_on", "accept", "agreed", "none")
# How long the call waits for the result when the manifest sets no ``timeout``.
DEFAULT_TIMEOUT_S = 1800.0


class RoomOpen:
    """Run ``open_room`` over the agent's link.

    ``comm`` is the agent's ``agent_comm`` link. It must have ``open_room``; a link without rooms
    (``local``) answers the model with an error text instead of failing the turn.
    """

    def __init__(
        self,
        *,
        comm: Any,
        channel: str,
        members: Sequence[str],
        rules: Mapping[str, Any],
        timeout: float | None = None,
        agent_id: str = "",
    ) -> None:
        self.comm = comm
        self.channel = channel
        self.members = [str(m) for m in members]
        self.rules = {k: v for k, v in rules.items() if k in RULE_KEYS}
        self.timeout = float(timeout) if timeout is not None else DEFAULT_TIMEOUT_S
        self.agent_id = agent_id

    def is_room_open_tool(self, tool_name: str) -> bool:
        return tool_name == OPEN_ROOM_TOOL

    def claims(self, tool_name: str) -> bool:
        return self.is_room_open_tool(tool_name)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        if not self.is_room_open_tool(tool_name):
            return f"[open_room] unsupported tool {tool_name!r}"
        task = str(arguments.get("task") or "").strip()
        if not task:
            return "[open_room] task must not be empty"
        opener = getattr(self.comm, "open_room", None)
        if not callable(opener):
            return "[open_room] this agent's link has no shared rooms"
        try:
            return str(opener(self.channel, task, members=self.members, rules=self.rules, timeout=self.timeout))
        except AgentCommError as exc:
            return f"[open_room] room {self.channel!r} failed: {exc}"

    async def acall(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        # open_room waits for the members, so keep it off the event loop
        return await asyncio.to_thread(
            self.call, tool_name, arguments, ctx=ctx, correlation_id=correlation_id, caller_call_id=caller_call_id
        )
