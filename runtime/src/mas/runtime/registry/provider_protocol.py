#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Protocol for pluggable tool providers."""

from typing import Any, Dict, List, Protocol, runtime_checkable


class ManifestToolLoadError(RuntimeError):
    """Raised when a provider cannot load or resolve a tool implementation."""


@runtime_checkable
class ToolProvider(Protocol):
    """Protocol for tool-provider plugins in the MAS runtime.

    Runtime calling is a name -> provider lookup. Plugins are registered first.
    ``discover_tools`` (optional) is the advertisement query used at runtime
    initialization when the claim is ``tools: "*"``. Explicit ``tools: [name, ...]``
    claims skip it; their presence can be checked at verification and at init
    via ``list_tools``. ``mas-ctl validate`` never queries ``*`` providers.
    """

    def has_tools(self) -> bool: ...

    def list_tools(self, *, ctx: Any = None) -> List[Dict[str, Any]]:
        """Return tool specifications this plugin currently serves."""
        ...

    # Calling is ``async def acall_tool(tool_name, arguments, *, ctx=None, user="", **kwargs)``. A provider that only
    # has the sync ``call_tool`` still works: the registry runs it on the worker threads. Neither is declared here,
    # so a class that names this protocol as a base does not inherit a stub that answers ``None``.
