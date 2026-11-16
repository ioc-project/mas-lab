#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Contracts for engine tools that cross into ctl orchestration."""

from __future__ import annotations

from typing import Any, Protocol


class EngineToolBudgetExceeded(RuntimeError):
    """A governed orchestration action was refused by the facade."""


class EngineToolContext(Protocol):
    """What an engine-tool plugin receives instead of the raw materialized run.

    Sized to the three operations `LlmDelegator.delegate()` and
    `SubagentSpawner.spawn()` actually perform. A plugin holding this cannot
    enumerate other agents, reach the comm bus, or skip the spawn ledger,
    because enforcement lives in the implementation of these methods rather
    than in plugin discipline.
    """

    async def arun_turn(
        self,
        agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        """Async twin of :meth:`run_turn`."""
        ...

    def spawn_instance(self, manifest: dict[str, Any], *, template_id: str) -> str:
        """Materialize a new child agent and return its minted id.

        Raises ``EngineToolBudgetExceeded`` rather than silently no-opping
        when the spawn ledger refuses.
        """
        ...

    def teardown_instance(self, agent_id: str) -> None:
        """Remove a child agent and release everything scoped to it."""
        ...

    @property
    def depth(self) -> int: ...

    @property
    def session_id(self) -> str: ...

    @property
    def parent_agent_id(self) -> str: ...


class EngineToolContract(Protocol):
    """Claim and execute one orchestration-level tool call."""

    def claims(self, tool_name: str, *, ctx: Any = None) -> bool: ...

    async def acall(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str: ...


class SubagentContract(Protocol):
    """Spawn a bounded, named subagent template for one task."""

    def is_subagent_tool(self, tool_name: str) -> bool: ...

    async def aspawn(
        self,
        template_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str: ...
