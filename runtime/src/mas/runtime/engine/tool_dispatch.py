#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine tool loop — delegation plugin and manifest tool provider dispatch."""

from __future__ import annotations

import contextvars
import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mas.runtime.boundary.connection import PreparedOperation
from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError
from mas.runtime.engine.tool_routing import ExplicitToolUnavailableError, UnclaimedToolError

if TYPE_CHECKING:
    from mas.runtime.boundary.delegation.protocol import DelegationContract
    from mas.runtime.boundary.engine_tools import EngineToolContract
    from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider


@dataclass(frozen=True)
class InFlightTool:
    """The tool call one engine is dispatching, so layers around it can name its target.

    ``owner`` is the dispatching engine (``id(engine)``): an engine run by a tool (a delegated child)
    sets its own and never reads its parent's.
    """

    owner: int
    name: str
    arguments: dict[str, Any]


IN_FLIGHT_TOOL: contextvars.ContextVar[InFlightTool | None] = contextvars.ContextVar(
    "mas_engine_tool_call",
    default=None,
)


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot be executed."""


class _DelegationEngineTool:
    """Adapt the existing DelegationContract to the ordered engine-tool seam."""

    def __init__(self, delegation: DelegationContract) -> None:
        self.delegation = delegation

    def claims(self, tool_name: str) -> bool:
        offers = getattr(self.delegation, "offers", None)
        if callable(offers):
            return bool(offers(tool_name))
        return self.delegation.is_delegate_tool(tool_name)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        return self.delegation.call_delegate_tool(
            tool_name,
            arguments,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
        )

    async def acall(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        adelegate = getattr(self.delegation, "acall_delegate_tool", None)
        if callable(adelegate):
            return await adelegate(
                tool_name,
                arguments,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
            )
        return self.call(
            tool_name,
            arguments,
            ctx=ctx,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
        )


def format_tool_result(result: Any, *, ctx: Any = None, call_id: str = "") -> str:
    """Format tool result as string for LLM consumption.

    A ``chunk`` key on a dict result is generic archive metadata — it is
    stashed on ``ctx`` and omitted from the model-facing JSON.
    """
    from mas.runtime.boundary.context.chunk_meta import pop_tool_chunk, stash_pending_chunk_meta

    payload, meta = pop_tool_chunk(result)
    if ctx is not None and meta:
        stash_pending_chunk_meta(ctx, call_id, meta)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return json.dumps(payload, indent=2)
    return str(payload)


def _sandbox_for(ctx: Any, sandbox: Any) -> Any:
    if sandbox is not None:
        return sandbox
    if ctx is not None:
        found = getattr(ctx, "execute_sandbox", None)
        if found is not None:
            return found
    from mas.runtime.boundary.sandbox import PassthroughSandbox

    return PassthroughSandbox()


def tool_is_idempotent(tool: str, spec: dict[str, Any] | None) -> bool:
    """True when *spec* declares tool *tool* ``idempotent``. A tool the spec does not list is not."""
    tools = (spec or {}).get("tools") if isinstance(spec, dict) else None
    if not isinstance(tools, list):
        return False
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "")
        if name != tool:
            continue
        inner = item.get("spec") if isinstance(item.get("spec"), dict) else item
        return bool(inner.get("idempotent"))
    return False


def _dispatch_once(
    tool: str,
    *,
    delegation: DelegationContract | None,
    engine_contracts: tuple[EngineToolContract, ...] | list[EngineToolContract],
    ctx: Any,
    user: str,
    arguments: dict[str, Any] | None,
    tool_provider: ManifestToolProvider | None,
    correlation_id: int,
    caller_call_id: str,
    prepared: PreparedOperation | None = None,
) -> str:
    if prepared is not None:
        if (
            tool_provider is not None
            and not (delegation is not None and delegation.is_delegate_tool(tool))
            and not any(c.claims(tool) for c in engine_contracts)
        ):
            tool_provider.assert_call_allowed(tool, ctx=ctx)
        return format_tool_result(
            prepared.invoke(correlation_id=correlation_id, caller_call_id=caller_call_id),
            ctx=ctx,
            call_id=caller_call_id,
        )
    contracts = list(engine_contracts)
    if delegation is not None:
        contracts.insert(0, _DelegationEngineTool(delegation))
    for contract in contracts:
        if contract.claims(tool):
            return contract.call(
                tool,
                arguments or {},
                ctx=ctx,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
            )
    if tool_provider is None:
        raise ToolExecutionError(f"No manifest tool provider configured; cannot execute {tool!r}")
    try:
        from mas.runtime.engine.tool_workers import provider_ctx

        result = tool_provider.call_tool(
            tool,
            arguments or {},
            ctx=provider_ctx(tool_provider, ctx),
            user=user,
        )
    except (ManifestToolLoadError, UnclaimedToolError, ExplicitToolUnavailableError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return format_tool_result(result, ctx=ctx, call_id=caller_call_id)


async def _acall_provider(
    provider: Any,
    tool: str,
    arguments: dict[str, Any] | None,
    *,
    ctx: Any,
    user: str,
    caller_call_id: str,
) -> str:
    from mas.runtime.contracts.tool_contract import invoke_call_tool

    try:
        acall_tool = getattr(provider, "acall_tool", None)
        if callable(acall_tool):
            from mas.runtime.engine.tool_workers import provider_ctx

            result = acall_tool(tool, arguments or {}, ctx=provider_ctx(provider, ctx), user=user)
            if inspect.isawaitable(result):
                result = await result
        else:
            from mas.runtime.engine.tool_workers import arun_sync_tool

            result = await arun_sync_tool(
                invoke_call_tool,
                provider.call_tool,
                tool,
                arguments or {},
                owner=provider,
                ctx=ctx,
                user=user,
            )
    except (ManifestToolLoadError, UnclaimedToolError, ExplicitToolUnavailableError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return format_tool_result(result, ctx=ctx, call_id=caller_call_id)


def execute_engine_tool(
    tool: str,
    *,
    delegation: DelegationContract | None = None,
    engine_contracts: tuple[EngineToolContract, ...] | list[EngineToolContract] = (),
    ctx: Any = None,
    user: str = "",
    arguments: dict[str, Any] | None = None,
    tool_provider: ManifestToolProvider | None = None,
    correlation_id: int = 0,
    caller_call_id: str = "",
    prepared: PreparedOperation | None = None,
    sandbox: Any = None,
) -> str:
    from mas.runtime.reliability.classes import ClassifiedFailure
    from mas.runtime.reliability.classify import classify_tool_exception

    box = _sandbox_for(ctx, sandbox)

    def _body() -> str:
        return _dispatch_once(
            tool,
            delegation=delegation,
            engine_contracts=engine_contracts,
            ctx=ctx,
            user=user,
            arguments=arguments,
            tool_provider=tool_provider,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            prepared=prepared,
        )

    try:
        return box.run(_body, tool_name=tool)
    except ClassifiedFailure:
        raise
    except Exception as exc:
        raise classify_tool_exception(exc) from exc


async def aexecute_engine_tool(
    tool: str,
    *,
    delegation: DelegationContract | None = None,
    engine_contracts: tuple[EngineToolContract, ...] | list[EngineToolContract] = (),
    ctx: Any = None,
    user: str = "",
    arguments: dict[str, Any] | None = None,
    tool_provider: ManifestToolProvider | None = None,
    correlation_id: int = 0,
    caller_call_id: str = "",
    prepared: PreparedOperation | None = None,
    sandbox: Any = None,
    placement: Any | None = None,
) -> str:
    """Async twin of :func:`execute_engine_tool`.

    Routes to ``acall`` / ``adelegate`` / ``aspawn`` when present so nested
    agent turns can overlap. Plugin tools use ``acall_tool``. An async
    ``on_execute_tool`` (or ``execute``) is awaited on the loop;
    ``on_aexecute_tool`` still wins when present. A sync method runs where the
    deployment says (``tool_workers``: inline or thread). The tool sees the same
    sealed context either way. ExecuteSandbox wraps the body after ALLOW; it is
    not a new envelope symbol. Retry is a layer around this invoke, not a loop
    in the engine.
    """
    from mas.runtime.reliability.classes import ClassifiedFailure
    from mas.runtime.reliability.classify import classify_tool_exception

    box = _sandbox_for(ctx, sandbox)
    if placement is not None:
        from mas.runtime.engine.tool_workers import bind_placement, unbind_placement

        token = bind_placement(placement)
        try:
            return await aexecute_engine_tool(
                tool,
                delegation=delegation,
                engine_contracts=engine_contracts,
                ctx=ctx,
                user=user,
                arguments=arguments,
                tool_provider=tool_provider,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
                sandbox=sandbox,
                prepared=prepared,
            )
        finally:
            unbind_placement(token)

    async def _body() -> str:
        if prepared is not None:
            if (
                tool_provider is not None
                and not (delegation is not None and delegation.is_delegate_tool(tool))
                and not any(c.claims(tool) for c in engine_contracts)
            ):
                tool_provider.assert_call_allowed(tool, ctx=ctx)
            result = await prepared.ainvoke(correlation_id=correlation_id, caller_call_id=caller_call_id)
            return format_tool_result(result, ctx=ctx, call_id=caller_call_id)
        contracts = list(engine_contracts)
        if delegation is not None:
            contracts.insert(0, _DelegationEngineTool(delegation))
        claimed_sync = False
        for contract in contracts:
            if contract.claims(tool):
                acall = getattr(contract, "acall", None)
                if callable(acall):
                    return await acall(
                        tool,
                        arguments or {},
                        ctx=ctx,
                        correlation_id=correlation_id,
                        caller_call_id=caller_call_id,
                    )
                claimed_sync = True
                break
        if claimed_sync or tool_provider is None:
            return _dispatch_once(
                tool,
                delegation=delegation,
                engine_contracts=engine_contracts,
                ctx=ctx,
                user=user,
                arguments=arguments,
                tool_provider=tool_provider,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
            )
        return await _acall_provider(
            tool_provider,
            tool,
            arguments,
            ctx=ctx,
            user=user,
            caller_call_id=caller_call_id,
        )

    try:
        return await box.arun(_body, tool_name=tool)
    except ClassifiedFailure:
        raise
    except Exception as exc:
        raise classify_tool_exception(exc) from exc
