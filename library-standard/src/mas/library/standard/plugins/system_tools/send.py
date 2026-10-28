#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One code path for ``delegate_to_<id>`` and a Python ``send`` call.

The model still sees only ``delegate_to_<id>``. This class executes those
calls. The target comes from the tool name; extra ``target`` or
``intent`` arguments are rejected.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any
from weakref import WeakKeyDictionary

from mas.library.standard.lib.comm.payload import intents_honoured_by
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.boundary.delegation.policy import (
    SEND_INTENTS,
    SEND_SEMANTICS,
    SEND_TOOL_NAME,
    openai_delegation_tools,
    parse_delegate_tool_name,
)

_Prepare = tuple[AgentCommContract, str, str, str]
_SEND_OPTIONAL = frozenset(
    {"correlation_id", "caller_call_id", "context_id", "headers", "context"}
)


def _accepted_kwargs(fn: Any) -> frozenset[str]:
    if not callable(fn):
        return frozenset()
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return frozenset()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return _SEND_OPTIONAL
    return frozenset(name for name in _SEND_OPTIONAL if name in params)


class SendTool:
    """Execute ``delegate_to_*``. ``send`` is not advertised to the model."""

    def __init__(
        self,
        *,
        comm: AgentCommContract | None = None,
        routes: dict[str, AgentCommRoute] | None = None,
        allowed_peers: Sequence[str] | None = None,
    ) -> None:
        self._comm = comm
        self._routes = dict(routes or {})
        self._allowed = frozenset(
            str(peer) for peer in (allowed_peers or ()) if str(peer).strip()
        )
        self._accepted: WeakKeyDictionary[Any, dict[str, frozenset[str]]] = WeakKeyDictionary()

    def set_routes(self, routes: dict[str, AgentCommRoute]) -> None:
        self._routes = dict(routes)

    def reset_session(self) -> None:
        return None

    @classmethod
    def collect_tool_specs(
        cls,
        manifest: dict | None,
        *,
        agent_id: str | None = None,
        peer_descriptions: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": spec["function"]["name"],
                "description": spec["function"]["description"],
                "parameters": spec["function"]["parameters"],
                "semantics": dict(SEND_SEMANTICS),
            }
            for spec in openai_delegation_tools(
                manifest,
                agent_id=agent_id,
                peer_descriptions=peer_descriptions,
            )
        ]

    @classmethod
    def collect_openai_tools(
        cls,
        manifest: dict | None,
        *,
        agent_id: str | None = None,
        peer_descriptions: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        return openai_delegation_tools(
            manifest,
            agent_id=agent_id,
            peer_descriptions=peer_descriptions,
        )

    def list_tools(self, *, ctx: Any = None, manifest: dict | None = None) -> list[dict[str, Any]]:
        doc = manifest
        agent_id = None
        if ctx is not None:
            agent_id = str(getattr(ctx, "agent_id", "") or "") or None
            if doc is None:
                raw = getattr(ctx, "manifest", None)
                if isinstance(raw, dict):
                    doc = raw
        return self.collect_tool_specs(doc, agent_id=agent_id)

    def on_collect_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        descriptions = None
        if ctx is not None:
            descriptions = getattr(ctx, "delegation_peer_descriptions", None)
            if descriptions is None:
                descriptions = getattr(ctx, "send_peer_descriptions", None)
        manifest = None
        agent_id = None
        if ctx is not None:
            raw = getattr(ctx, "manifest", None)
            if isinstance(raw, dict):
                manifest = raw
            agent_id = str(getattr(ctx, "agent_id", "") or "") or None
        return self.collect_tool_specs(
            manifest,
            agent_id=agent_id,
            peer_descriptions=descriptions if isinstance(descriptions, dict) else None,
        )

    def is_delegate_tool(self, tool_name: str) -> bool:
        return parse_delegate_tool_name(tool_name) is not None

    def is_send_tool(self, tool_name: str) -> bool:
        return tool_name == SEND_TOOL_NAME or self.is_delegate_tool(tool_name)

    def claims(self, tool_name: str) -> bool:
        return self.is_delegate_tool(tool_name)

    def _resolve(
        self,
        target_agent_id: str,
        *,
        ctx: Any = None,
    ) -> tuple[AgentCommContract, str]:
        routes = self._routes
        comm = self._comm
        if ctx is not None:
            ctx_routes = getattr(ctx, "agent_comm_routes", None)
            if isinstance(ctx_routes, dict) and ctx_routes:
                routes = ctx_routes
            ctx_comm = getattr(ctx, "agent_comm", None)
            if ctx_comm is not None:
                comm = ctx_comm
        route = routes.get(target_agent_id)
        if route is not None:
            self._inspect(route.handler)
            return route.handler, route.kind
        if comm is None:
            raise AgentCommError("send has no communication plugin")
        self._inspect(comm)
        return comm, "local-bus"

    def _inspect(self, communication: Any) -> dict[str, frozenset[str]]:
        try:
            cached = self._accepted.get(communication)
        except TypeError:
            cached = None
        if cached is not None:
            return cached
        send_fn = getattr(communication, "send", None)
        asend_fn = getattr(communication, "asend", None)
        names = {
            "send": _accepted_kwargs(send_fn),
            "asend": (
                _accepted_kwargs(asend_fn) if callable(asend_fn) else _accepted_kwargs(send_fn)
            ),
        }
        try:
            self._accepted[communication] = names
        except TypeError:
            pass
        return names

    def _prepare(
        self,
        target_agent_id: str,
        message: object,
        *,
        intent: str = "task",
        ctx: Any = None,
    ) -> _Prepare | str:
        target = str(target_agent_id or "").strip()
        if not target:
            return "[send] missing target"
        if not isinstance(message, str):
            return "[send] message must be a string"
        payload = message.strip()
        if not payload:
            return f"[send] missing message for {target!r}"
        kind = str(intent or "task").strip().lower() or "task"
        if kind not in SEND_INTENTS:
            return f"[send] unknown intent {intent!r}"
        if target not in self._allowed:
            return f"[send] target {target!r} is not an allowed peer"
        communication, protocol = self._resolve(target, ctx=ctx)
        if kind not in intents_honoured_by(communication):
            return f"[send] intent {kind!r} is not supported on this link"
        return communication, protocol, payload, kind

    def _send_kwargs(
        self,
        communication: Any,
        method: str,
        *,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
        headers: dict[str, str] | None,
        intent: str,
        mode: str = "",
    ) -> dict[str, Any]:
        accepted = self._inspect(communication)[method]
        extra: dict[str, Any] = {}
        if "correlation_id" in accepted:
            extra["correlation_id"] = correlation_id
        if "caller_call_id" in accepted:
            extra["caller_call_id"] = caller_call_id
        if "context_id" in accepted:
            extra["context_id"] = context_id
        if "context" in accepted:
            extra["context"] = SendContext(
                intent=intent, headers=dict(headers or {}), mode=mode
            )
        if headers is not None and "headers" in accepted:
            extra["headers"] = dict(headers)
        return extra

    def send(
        self,
        target_agent_id: str,
        message: str,
        *,
        intent: str = "task",
        mode: str = "",
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        ctx: Any = None,
    ) -> str:
        prepared = self._prepare(target_agent_id, message, intent=intent, ctx=ctx)
        if isinstance(prepared, str):
            return prepared
        communication, protocol, payload, kind = prepared
        extra = self._send_kwargs(
            communication,
            "send",
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            intent=kind,
            mode=mode,
        )
        try:
            result = communication.send(target_agent_id, payload, **extra)
        except AgentCommError as exc:
            return f"[send] agent {target_agent_id!r} via {protocol} failed: {exc}"
        except KeyError:
            return f"[send] agent {target_agent_id!r} not available on bus"
        except RuntimeError as exc:
            return f"[send] agent {target_agent_id!r} failed: {exc}"
        return result

    def delegate(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str:
        return self.send(
            target_agent_id,
            task,
            intent="task",
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
        )

    async def asend(
        self,
        target_agent_id: str,
        message: str,
        *,
        intent: str = "task",
        mode: str = "",
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        ctx: Any = None,
    ) -> str:
        prepared = self._prepare(target_agent_id, message, intent=intent, ctx=ctx)
        if isinstance(prepared, str):
            return prepared
        communication, protocol, payload, kind = prepared
        asend = getattr(communication, "asend", None)
        method = "asend" if callable(asend) else "send"
        extra = self._send_kwargs(
            communication,
            method,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            intent=kind,
            mode=mode,
        )
        try:
            if method == "asend":
                result = await asend(target_agent_id, payload, **extra)
            else:
                result = communication.send(target_agent_id, payload, **extra)
        except AgentCommError as exc:
            return f"[send] agent {target_agent_id!r} via {protocol} failed: {exc}"
        except KeyError:
            return f"[send] agent {target_agent_id!r} not available on bus"
        except RuntimeError as exc:
            return f"[send] agent {target_agent_id!r} failed: {exc}"
        return result

    async def adelegate(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str:
        return await self.asend(
            target_agent_id,
            task,
            intent="task",
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
        )

    def _args_to_send(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> tuple[str, str, str, str] | str:
        args = dict(arguments or {})
        named = parse_delegate_tool_name(tool_name)
        if named:
            extra = [key for key in ("target", "intent") if key in args]
            if extra:
                return f"[send] extra arguments {extra} not allowed on {tool_name!r}"
            raw = args.get("task")
            if raw is not None and not isinstance(raw, str):
                return "[send] message must be a string"
            return (
                named,
                str(raw or "").strip(),
                "task",
                str(args.get("context_id") or "").strip(),
            )
        raw = args.get("message")
        if raw is None:
            raw = args.get("task")
        if raw is not None and not isinstance(raw, str):
            return "[send] message must be a string"
        return (
            str(args.get("target") or "").strip(),
            str(raw or "").strip(),
            str(args.get("intent") or "task").strip().lower() or "task",
            str(args.get("context_id") or "").strip(),
        )

    def call_delegate_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        ctx: Any = None,
    ) -> str:
        if not self.is_send_tool(tool_name):
            return f"[send] not a send tool: {tool_name!r}"
        parsed = self._args_to_send(tool_name, arguments)
        if isinstance(parsed, str):
            return parsed
        target, message, intent, context_id = parsed
        return self.send(
            target,
            message,
            intent=intent,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            ctx=ctx,
        )

    call_send_tool = call_delegate_tool

    async def acall_delegate_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        ctx: Any = None,
    ) -> str:
        if not self.is_send_tool(tool_name):
            return f"[send] not a send tool: {tool_name!r}"
        parsed = self._args_to_send(tool_name, arguments)
        if isinstance(parsed, str):
            return parsed
        target, message, intent, context_id = parsed
        return await self.asend(
            target,
            message,
            intent=intent,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            ctx=ctx,
        )

    acall_send_tool = acall_delegate_tool

    def on_execute_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> str:
        return self.call_delegate_tool(
            tool_name,
            arguments,
            correlation_id=int(kwargs.get("correlation_id") or 0),
            caller_call_id=str(kwargs.get("caller_call_id") or ""),
            ctx=kwargs.get("ctx"),
        )

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        return self.call_delegate_tool(
            tool_name,
            arguments,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            ctx=ctx,
        )

    async def acall(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        ctx: Any = None,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        return await self.acall_delegate_tool(
            tool_name,
            arguments,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            ctx=ctx,
        )
