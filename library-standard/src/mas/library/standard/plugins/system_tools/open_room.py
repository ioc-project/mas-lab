#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""System tool advertisement for opening a shared room.

The agent declares ``{kind: system, name: open_room, params: {channel, members, ...rules}}``. The
tool is ``open_room(task)``: it creates the channel, opens a room on it with ``task``, and returns the
closer's result when the members have agreed or the room has stopped. The members take their turns on
their own and read each other's posts in the channel; the caller reads none of them. The call itself is
run by the ``room_open`` engine tool (``plugins/engine_tools/room_open.py``).
"""

from __future__ import annotations

from typing import Any

from mas.runtime.engine.tools import OPEN_ROOM_TOOL, open_room_params


class OpenRoomTool:
    """Advertise ``open_room`` when the manifest declares a channel and its members."""

    def on_collect_tools(self, *, ctx: Any = None, **_: Any) -> list[dict[str, Any]]:
        params = open_room_params(getattr(ctx, "current_spec", None))
        if not params or not params.get("channel") or not params.get("members"):
            return []
        members = ", ".join(str(m) for m in params["members"])
        about = str(params.get("description") or "").strip()
        return [
            {
                "name": OPEN_ROOM_TOOL,
                "description": (
                    f"Open the shared room {params['channel']!r} for {members} on a task. They read each "
                    "other's posts and answer round by round on their own; this returns the room's result "
                    "once they have settled or the room has stopped. " + about
                ).strip(),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task": {
                            "type": "string",
                            "minLength": 1,
                            "description": "What the room is asked to settle. The members share nothing else with you.",
                        },
                    },
                    "required": ["task"],
                },
            }
        ]

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> str:
        """Fail closed if engine-tool dispatch did not claim the call."""
        if tool_name != OPEN_ROOM_TOOL:
            return f"[open_room] unsupported tool {tool_name!r}"
        return "[open_room] unavailable: the agent has no room link"
