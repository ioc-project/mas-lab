#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""System tool advertisement for bounded, pre-authored subagent templates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mas.runtime.engine.tools import CREATE_SUBAGENT_TOOL, SPAWN_SUBAGENT_NAMES
from mas.runtime.system_plugins import (
    CallContext,
    InstanceSystemPlugin,
    SessionContext,
    SpawnLedger,
    ToolDefinition,
    UnknownSystemToolError,
)


def advertise_create_subagent(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``create_subagent`` advertise entry for these templates, or nothing without templates."""
    if not templates:
        return []
    template_ids = [str(template["id"]) for template in templates]
    descriptions = [f"{template['id']}: {template.get('description', '')}".rstrip(": ") for template in templates]
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template": {
                "type": "string",
                "enum": template_ids,
                "description": "Declared subagent template to create and run.",
            },
            "task": {
                "type": "string",
                "minLength": 1,
                "description": "Task for the child. The child cannot invent tools or templates.",
            },
        },
        "required": ["template", "task"],
    }
    catalog = "; ".join(descriptions)
    return [
        {
            "name": CREATE_SUBAGENT_TOOL,
            "description": (
                "Create a bounded subagent from a declared template and run it for one turn. Templates: " + catalog
            ),
            "parameters": parameters,
        },
    ]


class SpawnSubagentTool:
    """Advertise create_subagent when the manifest grants the capability."""

    def on_collect_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        if not getattr(ctx, "allow_subagent_spawning", False):
            return []
        return advertise_create_subagent(list(getattr(ctx, "subagent_templates", []) or []))

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> str:
        """Fail closed if engine-tool dispatch did not claim the call."""
        if tool_name not in SPAWN_SUBAGENT_NAMES:
            return f"[spawn_subagent] unsupported tool {tool_name!r}"
        return "[spawn_subagent] unavailable: orchestration contract is not wired"


@dataclass
class SubagentsState:
    """Session bind state: the spawn ledger, the advertise tool and the host-provided spawner.

    ``spawner`` is set by the host that can materialize children (the session executor): a contract
    with ``call`` / ``acall`` that runs one template for one turn against ``ledger``. Without it the
    plugin fails closed.
    """

    ledger: SpawnLedger
    tool: SpawnSubagentTool
    spawner: Any = None

    def adopt(self, contract: Any) -> bool:
        """Take over a spawner contract the host put on the engine before this plugin was bound."""
        is_spawn = getattr(contract, "is_subagent_tool", None)
        if self.spawner is not None or not callable(is_spawn) or not is_spawn(CREATE_SUBAGENT_TOOL):
            return False
        self.spawner = contract
        ledger = getattr(getattr(contract, "context", None), "ledger", None)
        if isinstance(ledger, SpawnLedger):
            self.ledger = ledger
        return True


class SubagentsPlugin(InstanceSystemPlugin):
    """Bundle: ``create_subagent`` over this session's spawn ledger.

    The plugin owns the ledger (templates, depth and count budgets, stable subagent identities) and the
    call. The host binds a spawner on the state (``SubagentsState.spawner``) because only it can build a
    child runtime; the plugin checks the tool, then runs the spawner. Settings: ``templates``,
    ``max_spawns``, ``max_depth``.
    """

    name = "subagents"

    def __init__(self, **settings: Any) -> None:
        super().__init__()
        self.settings = dict(settings)
        self._state: SubagentsState | None = None

    def make_instance(self, session: SessionContext | None) -> SpawnSubagentTool:
        return SpawnSubagentTool()

    def _ledger(self) -> SpawnLedger:
        kwargs: dict[str, Any] = {}
        if "max_depth" in self.settings:
            kwargs["max_depth"] = int(self.settings["max_depth"])
        if "max_spawns" in self.settings:
            kwargs["max_spawns"] = int(self.settings["max_spawns"])
        return SpawnLedger(**kwargs)

    def bind(self, session: SessionContext) -> object:
        tool = self.make_instance(session)
        self._bound = tool
        self._state = SubagentsState(ledger=self._ledger(), tool=tool)
        return self._state

    def snapshot(self, state: object) -> Mapping[str, Any]:
        ledger = getattr(state, "ledger", None)
        return dict(ledger.snapshot()) if isinstance(ledger, SpawnLedger) else {}

    def restore(self, snapshot: Mapping[str, Any]) -> object:
        if self._state is None:
            raise RuntimeError(f"{type(self).__name__}.restore called before bind")
        # in place: the spawner and the host hold this ledger. The spec owns the ceilings; the
        # snapshot only carries what was spent.
        self._state.ledger.restore_spend(snapshot)
        return self._state

    def tools(self, ctx: Any | None = None):
        templates = list(getattr(ctx, "subagent_templates", None) or self.settings.get("templates") or [])
        return tuple(ToolDefinition.from_advertise(spec) for spec in advertise_create_subagent(templates))

    def _spawner(self, tool: str, state: object) -> Any:
        if tool not in SPAWN_SUBAGENT_NAMES:
            raise UnknownSystemToolError(tool)
        return getattr(state, "spawner", None)

    def call(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> Any:
        spawner = self._spawner(tool, state)
        if spawner is None:
            return SpawnSubagentTool().on_execute_tool(tool, dict(args), ctx=context.ctx)
        return spawner.call(
            tool,
            dict(args),
            ctx=context.ctx,
            correlation_id=context.correlation_id,
            caller_call_id=context.caller_call_id,
        )

    async def acall(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> Any:
        spawner = self._spawner(tool, state)
        if spawner is None:
            return SpawnSubagentTool().on_execute_tool(tool, dict(args), ctx=context.ctx)
        return await spawner.acall(
            tool,
            dict(args),
            ctx=context.ctx,
            correlation_id=context.correlation_id,
            caller_call_id=context.caller_call_id,
        )
