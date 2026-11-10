#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLM advertisement over ControlContract. The tool is not the implementation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mas.runtime.engine.tools import CONTROL_TOOLS
from mas.runtime.system_plugins import (
    CallContext,
    InstanceSystemPlugin,
    SessionContext,
    ToolDefinition,
    UnknownSystemToolError,
)


def control_advertise_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "pause_session",
            "description": "Pause this session at the next turn boundary.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
        {
            "name": "list_checkpoints",
            "description": "List snapshot-tree nodes for this session.",
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        },
        {
            "name": "inspect_session",
            "description": "Inspect live vs debug-cursor snapshot ids for this session.",
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        },
        {
            "name": "navigate_checkpoint",
            "description": "Move the debug cursor. Does not change the live run until promote.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "to": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["to", "reason"],
            },
        },
        {
            "name": "cancel_inflight",
            "description": "Cancel the in-flight LLM call. Tools are not rolled back.",
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        },
        {
            "name": "run_control_script",
            "description": (
                "Run a gdb-like control script (pause, persist, inspect, steer). "
                "Pass inline text or @path, same as mas-ctl control --data."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "script": {"type": "string"},
                    "auto_stop": {"type": "boolean"},
                },
                "required": ["script"],
            },
        },
    ]


class ControlTools:
    """Bounded LLM surface: pause, inspect, list, navigate, cancel — same SessionControl."""

    def __init__(self, control: Any = None, session_id: str = "") -> None:
        self.control = control
        self.session_id = session_id

    def _bound(self, ctx: Any | None) -> tuple[Any, str]:
        control = self.control or getattr(ctx, "control", None)
        session_id = self.session_id or str(getattr(ctx, "session_id", "") or "")
        return control, session_id

    def on_collect_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        if not getattr(ctx, "allow_control_tools", False):
            return []
        control, session_id = self._bound(ctx)
        if control is None or not session_id:
            return []
        return control_advertise_specs()

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], ctx: Any = None, **_: Any) -> str:
        control, session_id = self._bound(ctx)
        if control is None or not session_id:
            return "[control] unavailable: control contract is not wired"
        if tool_name == "pause_session":
            control.pause(session_id, reason=str(arguments.get("reason") or ""))
            return "paused"
        if tool_name == "list_checkpoints":
            nodes = control.list_checkpoints(session_id)
            return ",".join(n.snapshot_id for n in nodes)
        if tool_name == "inspect_session":
            view = control.inspect(session_id)
            return json.dumps(
                {
                    "session_id": view.session_id,
                    "status": view.status,
                    "turn": view.turn,
                    "live_snapshot_id": view.live_snapshot_id,
                    "cursor_snapshot_id": view.cursor_snapshot_id,
                    "spec_revision": view.spec_revision,
                }
            )
        if tool_name == "navigate_checkpoint":
            ref = control.navigate(
                session_id,
                to=str(arguments.get("to") or ""),
                reason=str(arguments.get("reason") or ""),
            )
            return getattr(ref, "snapshot_id", str(ref))
        if tool_name == "cancel_inflight":
            cancelled = control.cancel_inflight(session_id)
            return "cancelled" if cancelled else "idle"
        if tool_name == "run_control_script":
            result = control.run_script(
                session_id,
                text=str(arguments.get("script") or ""),
                auto_stop=bool(arguments.get("auto_stop")),
            )
            return json.dumps(result, default=str)
        return f"[control] unsupported tool {tool_name!r}"


class PauseSessionPlugin(InstanceSystemPlugin):
    """System plugin named ``pause_session``. Advertises the control-tool bundle."""

    name = "pause_session"

    def make_instance(self, session: SessionContext | None) -> ControlTools:
        return ControlTools(session_id=session.session_id if session is not None else "")

    def tools(self, ctx: Any | None = None):
        if ctx is None:
            return tuple(ToolDefinition.from_advertise(spec) for spec in control_advertise_specs())
        return super().tools(ctx)

    def call(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> Any:
        if tool not in CONTROL_TOOLS:
            raise UnknownSystemToolError(tool)
        assert isinstance(state, ControlTools)
        return state.on_execute_tool(tool, dict(args), ctx=context.ctx)
