#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Wrappers that catch the control-flow signal of ``request_human_input`` and ``inform_user``."""

from __future__ import annotations

import logging
from typing import Any

from mas.runtime.contracts.user_communication_contract import HITLContract, UserIOContract

logger = logging.getLogger(__name__)


class SystemToolWrapperBase:
    """Shared boilerplate for system-tool wrappers that catch a control-flow signal.

    `request_human_input` (blocking HITL) and `inform_user` (non-blocking status
    update) both wrap a plain `ToolContract` instance, forward tool collection
    and normal execution unchanged, and only diverge once their tool raises its
    own sentinel signal. That common plumbing lives here so the two wrappers
    only need to implement their signal-handling branch.
    """

    def __init__(self, tool_instance: Any) -> None:
        self._tool = tool_instance

    def on_collect_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Forward tool collection to wrapped instance.

        Accepts and forwards arbitrary kwargs (e.g. ``ctx``) so a wrapped
        tool that needs runtime context to build its schema (see
        SkillToolsPlugin.on_collect_tools) still gets it through this
        wrapper -- LocalToolProvider.list_tools/call_tool always pass
        ``ctx=`` now.
        """
        if hasattr(self._tool, "on_collect_tools"):
            return self._tool.on_collect_tools(**kwargs)
        # Fallback to legacy API
        try:
            return [
                {
                    "name": self._tool.get_name(),
                    "description": self._tool.get_description(),
                    "parameters": self._tool.get_parameters_schema(),
                }
            ]
        except (AttributeError, NotImplementedError):
            return []

    def _execute_wrapped(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any,
        user: str,
    ) -> Any:
        """Run the wrapped tool's normal (non-signal) execution path."""
        execute_hook = getattr(self._tool, "on_execute_tool", None)
        if callable(execute_hook):
            return execute_hook(tool_name, arguments, ctx=ctx, user=user)
        return self._tool.execute(**arguments)

    @staticmethod
    def _extract_context(ctx: Any) -> tuple[str, str, int]:
        """Pull (session_id, agent_id, correlation_id) off the call context."""
        return (
            getattr(ctx, "session_id", "unknown"),
            getattr(ctx, "agent_id", "unknown"),
            getattr(ctx, "correlation_id", 0),
        )


class SystemToolHitlWrapper(SystemToolWrapperBase):
    """Wrapper for system tools that emit HITL signals.

    Catches RequestHitlSignal and resolves it through ``hitl_contract`` --
    ``RegistryHitlContract`` by default (register in the shared
    HitlResolverRegistry and BLOCK until an external resolver provides the
    user's response), or ``AutoResolveHitlContract`` when the host runs
    unattended (batch CLI, benchmarks).

    Timeout handling (RegistryHitlContract path):
    - The call's own `timeout` argument wins; otherwise `default_timeout_seconds`
      (the manifest-configured default, if any) applies; with neither set, the
      wait has no timeout at all.
    - If a timeout is set and elapses before resolution → raises TimeoutError
    - No auto-approval (user must explicitly respond)
    """

    def __init__(
        self,
        tool_instance: Any,
        hitl_contract: HITLContract | None = None,
        *,
        default_timeout_seconds: float | None = None,
        auto_resolve_decision: str | None = None,
    ) -> None:
        super().__init__(tool_instance)
        from mas.library.standard.plugins.hitl.contracts import (
            AutoResolveHitlContract,
            RegistryHitlContract,
        )

        if hitl_contract is None:
            hitl_contract = RegistryHitlContract()
        elif isinstance(hitl_contract, AutoResolveHitlContract) and auto_resolve_decision:
            hitl_contract = AutoResolveHitlContract(auto_resolve_decision)
        self._hitl_contract: HITLContract = hitl_contract
        self._default_timeout_seconds = default_timeout_seconds

    def on_execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
    ) -> Any:
        """Execute tool and catch HITL signal.

        If the tool raises RequestHitlSignal, resolve it via
        self._hitl_contract.request_approval().
        """
        from mas.runtime.system_tools.signal import RequestHitlSignal

        try:
            return self._execute_wrapped(tool_name, arguments, ctx=ctx, user=user)
        except RequestHitlSignal as signal:
            session_id, agent_id, correlation_id = self._extract_context(ctx)

            timeout_seconds = signal.timeout if signal.timeout is not None else self._default_timeout_seconds
            logger.info(
                f"Agent {agent_id} awaiting HITL resolution "
                f"(timeout={timeout_seconds if timeout_seconds is not None else 'none'}s): "
                f"{signal.question}"
            )
            result = self._hitl_contract.request_approval(
                question=signal.question,
                session_id=session_id,
                requesting_user_id=user,
                agent_id=agent_id,
                correlation_id=correlation_id,
                question_type=signal.question_type.value,
                choices=signal.choices,
                context_data=signal.context_data,
                timeout=timeout_seconds,
            )
            logger.info("Agent %s HITL resolved: user chose '%s'", agent_id, result.get("choice"))
            return result


class SystemToolUserUpdateWrapper(SystemToolWrapperBase):
    """Wrapper for system tools that emit non-blocking user status updates.

    Catches InformUserSignal and routes it through
    user_io_contract.send_progress_update() -- defaults to RegistryUserIOContract
    (register in the shared HitlResolverRegistry's user-update channel, e.g.
    polled by an external integration) unless a different UserIOContract was supplied.

    Unlike `SystemToolHitlWrapper`, this never blocks: the tool call returns
    immediately regardless of which implementation consumes the update.
    """

    def __init__(self, tool_instance: Any, user_io_contract: UserIOContract | None = None) -> None:
        super().__init__(tool_instance)
        if user_io_contract is None:
            from mas.library.standard.plugins.hitl.contracts import (
                RegistryUserIOContract,
            )

            user_io_contract = RegistryUserIOContract()
        self._user_io_contract: UserIOContract = user_io_contract

    def on_execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
    ) -> Any:
        """Execute tool and catch the non-blocking update signal."""
        from mas.runtime.system_tools.signal import InformUserSignal

        try:
            return self._execute_wrapped(tool_name, arguments, ctx=ctx, user=user)
        except InformUserSignal as signal:
            session_id, agent_id, correlation_id = self._extract_context(ctx)

            receipt = self._user_io_contract.send_progress_update(
                message=signal.message,
                session_id=session_id,
                requesting_user_id=signal.user_name or user,
                agent_id=agent_id,
                correlation_id=correlation_id,
                involved_agents=signal.involved_agents,
                metadata=signal.metadata,
            )
            logger.info(
                "Agent %s sent progress update for session=%s: %s",
                agent_id,
                session_id,
                signal.message,
            )
            return {
                "status": "sent",
                "message": signal.message,
                "user_name": signal.user_name or user,
                "involved_agents": list(signal.involved_agents),
                "metadata": dict(signal.metadata),
                "blocking": False,
                "receipt": receipt,
            }
