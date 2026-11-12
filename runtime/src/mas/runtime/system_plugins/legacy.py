#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Adapter for the deprecated ``system_tool`` plugin type.

A ``system_tool`` row was one tool object (``on_collect_tools`` / ``on_execute_tool``) that the local tool provider
wrapped. It still loads: this adapter makes it a system plugin. The row's ``communication`` attribute
(``hitl`` / ``user_io``) still wraps the tool when the host supplies ``SessionContext.wrap_system_tool``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from mas.runtime.system_plugins.contract import InstanceSystemPlugin, SessionContext, ToolInstance

LEGACY_SYSTEM_TOOL = "system_tool"


class LegacySystemToolPlugin(InstanceSystemPlugin):
    tool_cls: ClassVar[type]
    communication: ClassVar[str | None] = None

    def __init__(self, **settings: Any) -> None:
        super().__init__()
        self.settings = dict(settings)

    def make_instance(self, session: SessionContext | None) -> ToolInstance:
        tool = self.tool_cls(**self.settings)
        wrap = None if session is None else session.wrap_system_tool
        if wrap is None or not self.communication:
            return tool
        return wrap(tool, self.communication)


def adapt_system_tool(
    tool_cls: type,
    name: str,
    *,
    communication: str | None = None,
) -> type[LegacySystemToolPlugin]:
    return type(
        f"Legacy_{name}",
        (LegacySystemToolPlugin,),
        {"tool_cls": tool_cls, "name": name, "communication": communication},
    )
