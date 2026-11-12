#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Contract a system plugin implements. No filesystem, no env."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

ToolResult = Any


class UnknownSystemToolError(KeyError):
    """``call`` received a tool name this plugin does not expose."""


@dataclass(frozen=True)
class ToolDefinition:
    """Advertise entry plus the governance hints ``ToolContract`` already carries."""

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    is_destructive: bool | None = None
    is_idempotent: bool | None = None
    is_read_only: bool | None = None
    is_open_world: bool | None = None

    def as_advertise(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }
        if self.is_idempotent:
            entry["idempotent"] = True
        if self.is_read_only is not None:
            entry["read_only"] = self.is_read_only
        if self.is_destructive is not None:
            entry["destructive"] = self.is_destructive
        if self.is_open_world is not None:
            entry["open_world"] = self.is_open_world
        return entry

    @classmethod
    def from_advertise(cls, spec: Mapping[str, Any]) -> ToolDefinition:
        return cls(
            name=str(spec.get("name") or ""),
            description=str(spec.get("description") or ""),
            parameters=dict(spec.get("parameters") or {}),
            is_destructive=spec.get("destructive"),
            is_idempotent=True if spec.get("idempotent") else None,
            is_read_only=spec.get("read_only"),
            is_open_world=spec.get("open_world"),
        )


@dataclass
class SessionContext:
    """What ``bind`` may read. One of these per session; never stored on a shared instance."""

    spec: Mapping[str, Any] = field(default_factory=dict)
    session_id: str = ""
    agent_id: str = ""
    hitl_contract: Any = None
    user_io_contract: Any = None
    skills_dir: Path | None = None
    auto_inject_scripts: bool = False
    wrap_system_tool: Any = None


@dataclass
class CallContext:
    """What ``call`` may read. ``ctx`` is the engine turn context when one exists."""

    ctx: Any = None
    user: str = ""
    correlation_id: int = 0
    caller_call_id: str = ""


class SystemPluginBase:
    """Defaults for a system plugin. A plugin implements ``tools``, ``bind`` and ``call``.

    A plugin loads when ``spec.system_plugins`` names it, or when its registry row has
    ``default_enabled: true`` and it is not named with ``enabled: false``. ``should_load`` is the other way a
    plugin is granted without being named: a rule over the agent spec (``activate_skill`` is granted by
    ``spec.skills``). ``enabled: false`` turns off both.
    """

    name: ClassVar[str] = ""

    @classmethod
    def should_load(cls, spec: Mapping[str, Any], settings: Mapping[str, Any], *, session: SessionContext) -> bool:
        return False

    def tools(self, ctx: Any | None = None) -> Sequence[ToolDefinition]:
        raise NotImplementedError(f"{type(self).__name__} must implement tools")

    def bind(self, session: SessionContext) -> object:
        raise NotImplementedError(f"{type(self).__name__} must implement bind")

    def claims(self, tool: str, *, state: object) -> bool:
        return any(definition.name == tool for definition in self.tools())

    def call(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> ToolResult:
        raise NotImplementedError(f"{type(self).__name__} must implement call")

    async def acall(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> ToolResult:
        """Async twin of :meth:`call`. A plugin that does network I/O overrides it."""
        return self.call(tool, args, state=state, context=context)

    def close(self, state: object) -> None:
        return None

    def snapshot(self, state: object) -> Mapping[str, Any]:
        return {}

    def restore(self, snapshot: Mapping[str, Any]) -> object:
        raise NotImplementedError(f"{type(self).__name__} keeps state it cannot restore from a snapshot")


class ToolInstance(Protocol):
    """A tool object: what ``LocalToolProvider`` already calls on application tools."""

    def on_collect_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]: ...

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> Any: ...


class InstanceSystemPlugin(SystemPluginBase):
    """A system plugin over one :class:`ToolInstance` (N tools, one state).

    ``make_instance(session)`` builds the state; ``None`` asks for an unbound instance that is only
    used to list tools before ``bind``.
    """

    def __init__(self, **_: Any) -> None:
        self._bound: ToolInstance | None = None

    def make_instance(self, session: SessionContext | None) -> ToolInstance:
        raise NotImplementedError(f"{type(self).__name__} must implement make_instance")

    def _instance(self) -> ToolInstance:
        return self._bound if self._bound is not None else self.make_instance(None)

    def tools(self, ctx: Any | None = None) -> Sequence[ToolDefinition]:
        return tuple(ToolDefinition.from_advertise(spec) for spec in self._instance().on_collect_tools(ctx=ctx) or ())

    def bind(self, session: SessionContext) -> object:
        self._bound = self.make_instance(session)
        return self._bound

    def call(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        state: object,
        context: CallContext,
    ) -> ToolResult:
        instance: ToolInstance = state  # type: ignore[assignment]
        names = {str(spec.get("name") or "") for spec in instance.on_collect_tools(ctx=context.ctx) or ()}
        if tool not in names:
            raise UnknownSystemToolError(tool)
        return instance.on_execute_tool(tool, dict(args), ctx=context.ctx, user=context.user)

    def restore(self, snapshot: Mapping[str, Any]) -> object:
        if self._bound is None:
            raise RuntimeError(f"{type(self).__name__}.restore called before bind")
        if snapshot:
            raise NotImplementedError(f"{type(self).__name__} keeps state it cannot restore from a snapshot")
        return self._bound
