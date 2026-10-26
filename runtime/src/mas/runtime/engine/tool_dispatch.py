#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine tool loop — delegation plugin and manifest tool provider dispatch."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError
from mas.runtime.engine.tool_routing import ExplicitToolUnavailableError, UnclaimedToolError

if TYPE_CHECKING:
    from mas.runtime.boundary.delegation.protocol import DelegationContract
    from mas.runtime.boundary.engine_tools import EngineToolContract
    from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot be executed."""


class _DelegationEngineTool:
    """Adapt the existing DelegationContract to the ordered engine-tool seam."""

    def __init__(self, delegation: DelegationContract) -> None:
        self.delegation = delegation

    def claims(self, tool_name: str) -> bool:
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


def _idempotent_from_spec(tool: str, spec: dict[str, Any] | None) -> bool:
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
) -> str:
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
        result = tool_provider.call_tool(
            tool,
            arguments or {},
            ctx=ctx,
            user=user,
        )
    except (ManifestToolLoadError, UnclaimedToolError, ExplicitToolUnavailableError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return format_tool_result(result, ctx=ctx, call_id=caller_call_id)


def _call_with_tool_retry(
    once,
    *,
    tool: str,
    retry_policy: Any | None,
    circuit_breaker: Any | None,
    agent_spec: dict[str, Any] | None,
):
    from mas.runtime.reliability.classify import classify_tool_exception
    from mas.runtime.reliability.policy import RetryPolicy
    from mas.runtime.reliability.retry import call_with_retry

    policy = retry_policy or RetryPolicy.tools_default()
    return call_with_retry(
        once,
        policy=policy,
        classify=classify_tool_exception,
        breaker=circuit_breaker,
        target=f"tool:{tool}",
        idempotent=_idempotent_from_spec(tool, agent_spec),
    )


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
    sandbox: Any = None,
    retry_policy: Any | None = None,
    circuit_breaker: Any | None = None,
    agent_spec: dict[str, Any] | None = None,
) -> str:
    box = _sandbox_for(ctx, sandbox)

    def _body() -> str:
        return _call_with_tool_retry(
            lambda: _dispatch_once(
                tool,
                delegation=delegation,
                engine_contracts=engine_contracts,
                ctx=ctx,
                user=user,
                arguments=arguments,
                tool_provider=tool_provider,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
            ),
            tool=tool,
            retry_policy=retry_policy,
            circuit_breaker=circuit_breaker,
            agent_spec=agent_spec,
        )

    return box.run(_body, tool_name=tool)


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
    sandbox: Any = None,
    retry_policy: Any | None = None,
    circuit_breaker: Any | None = None,
    agent_spec: dict[str, Any] | None = None,
) -> str:
    """Async twin of :func:`execute_engine_tool`.

    Routes to ``acall`` / ``adelegate`` / ``aspawn`` when present so nested
    agent turns can overlap. Local manifest tools stay on the sync
    ``call_tool`` path — they are not network I/O. ExecuteSandbox wraps the
    body after ALLOW; it is not a new envelope symbol. Infra retries are
    engine I/O inside that body, not a kernel op.
    """
    box = _sandbox_for(ctx, sandbox)

    async def _body() -> str:
        contracts = list(engine_contracts)
        if delegation is not None:
            contracts.insert(0, _DelegationEngineTool(delegation))
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
                break
        return _call_with_tool_retry(
            lambda: _dispatch_once(
                tool,
                delegation=delegation,
                engine_contracts=engine_contracts,
                ctx=ctx,
                user=user,
                arguments=arguments,
                tool_provider=tool_provider,
                correlation_id=correlation_id,
                caller_call_id=caller_call_id,
            ),
            tool=tool,
            retry_policy=retry_policy,
            circuit_breaker=circuit_breaker,
            agent_spec=agent_spec,
        )

    return await box.arun(_body, tool_name=tool)
