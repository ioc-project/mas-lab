#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Delegation plugin contract — ``delegate_to_*`` tool execution over CommBus."""

from __future__ import annotations

from typing import Any, Protocol


class DelegationContract(Protocol):
    """Execute peer delegation tools surfaced by MAS ``workflow.delegates_to``."""

    def is_delegate_tool(self, tool_name: str) -> bool: ...

    async def adelegate(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str: ...

    async def acall_delegate_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str: ...
