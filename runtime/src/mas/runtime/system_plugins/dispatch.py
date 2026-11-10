#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Route a tool call to the system plugin that registered it."""

from __future__ import annotations

from typing import Any

from mas.runtime.system_plugins.contract import CallContext
from mas.runtime.system_plugins.load import BoundSystemPlugins


class SystemPluginDispatcher:
    """``EngineToolContract`` over a bound set. Application ToolProviders never see these names."""

    def __init__(self, bound: BoundSystemPlugins) -> None:
        self.bound = bound

    def claims(self, tool_name: str) -> bool:
        return self.bound.claims(tool_name)

    @staticmethod
    def _context(ctx: Any, user: str, correlation_id: int, caller_call_id: str) -> CallContext:
        return CallContext(
            ctx=ctx,
            user=user or str(getattr(ctx, "user", "") or ""),
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
        )

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
        user: str = "",
        **_: Any,
    ) -> Any:
        return self.bound.call(tool_name, arguments, context=self._context(ctx, user, correlation_id, caller_call_id))

    async def acall(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
        user: str = "",
        **_: Any,
    ) -> Any:
        return await self.bound.acall(
            tool_name, arguments, context=self._context(ctx, user, correlation_id, caller_call_id)
        )
