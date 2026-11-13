# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Optional resolved connections and atomic admission to a captured operation.

A binding is a private target plus invocation callbacks, not a persistent socket.
Replacing it invalidates pending attempts; admitted attempts retain their target.
"""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class BindingUnavailable(RuntimeError):
    """A prepared operation is closed, retired, or no longer matches its call."""


@dataclass(frozen=True, repr=False)
class ConnectionTarget:
    """Private target selector. Never serialize this into model input or telemetry."""

    endpoint: str = ""
    command: tuple[str, ...] = ()
    cwd: str = ""
    principal: str = ""


@dataclass(frozen=True)
class ConnectionInfo:
    protocol: str
    transport: str
    binding_id: str
    generation: int
    _matches_target: Callable[[ConnectionTarget], bool] = field(repr=False, compare=False)
    binding_ref: str = ""

    def matches_target(self, target: ConnectionTarget) -> bool:
        """Compare protected configuration without exposing the resolved target."""
        return self._matches_target(target)


@dataclass(frozen=True, repr=False)
class Invocation:
    call: Callable[..., Any]
    acall: Callable[..., Awaitable[Any]] | None = None


InvocationFactory = Callable[[str, dict[str, Any], Any], Invocation]


class ConnectionBinding:
    """One replaceable binding; the guard never spans policy or network work."""

    def __init__(
        self,
        protocol: str,
        transport: str,
        target: ConnectionTarget,
        factory: InvocationFactory,
        *,
        binding_ref: str = "",
    ) -> None:
        self._guard = threading.Lock()
        self._id = uuid.uuid4().hex
        self._binding_ref = binding_ref
        self._generation = 0
        self._closed = False
        self._protocol = protocol
        self._transport = transport
        self._target = target
        self._factory = factory

    def replace(self, protocol: str, transport: str, target: ConnectionTarget, factory: InvocationFactory) -> None:
        with self._guard:
            if self._closed:
                raise BindingUnavailable("Connection binding is closed")
            self._generation += 1
            self._protocol, self._transport, self._target, self._factory = protocol, transport, target, factory

    def close(self) -> None:
        with self._guard:
            self._closed = True
            self._generation += 1

    def prepare(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        factory: InvocationFactory | None = None,
    ) -> PreparedOperation:
        with self._guard:
            if self._closed:
                raise BindingUnavailable("Connection binding is closed")
            # A factory only captures configuration/arguments. Discovery and SDK
            # negotiation must finish before the binding is registered.
            invocation = (factory or self._factory)(tool_name, copy.deepcopy(arguments), ctx)
            info = ConnectionInfo(
                self._protocol,
                self._transport,
                self._id,
                self._generation,
                lambda expected, target=self._target: expected == target,
                self._binding_ref,
            )
            return PreparedOperation(self, info, invocation.call, invocation.acall)


class PreparedOperation:
    """An invocation pinned to its binding and callbacks until explicit close.

    Provider callbacks must capture the same immutable target used by the binding.
    They must not perform a fresh lookup or renegotiate a different interface.
    """

    def __init__(
        self,
        owner: ConnectionBinding,
        connection: ConnectionInfo,
        invoke: Callable[..., Any],
        ainvoke: Callable[..., Awaitable[Any]] | None,
    ) -> None:
        self._connection = connection
        self._owner = owner
        self._invoke = invoke
        self._ainvoke = ainvoke
        self._closed = False

    @property
    def connection(self) -> ConnectionInfo:
        return self._connection

    def _admit(self) -> None:
        # This is the invocation's linearization point. Replacement shares the
        # guard; callbacks below are already captured and cannot be retargeted.
        with self._owner._guard:
            if self._closed or self._owner._closed or self.connection.generation != self._owner._generation:
                raise BindingUnavailable("Prepared connection was closed or replaced")

    def invoke(self, *, correlation_id: int = 0, caller_call_id: str = "") -> Any:
        self._admit()
        trace = (
            {"correlation_id": correlation_id, "caller_call_id": caller_call_id}
            if correlation_id or caller_call_id
            else {}
        )
        return self._invoke(**trace)

    async def ainvoke(self, *, correlation_id: int = 0, caller_call_id: str = "") -> Any:
        self._admit()
        trace = (
            {"correlation_id": correlation_id, "caller_call_id": caller_call_id}
            if correlation_id or caller_call_id
            else {}
        )
        if self._ainvoke is None:
            return self._invoke(**trace)
        return await self._ainvoke(**trace)

    def close(self) -> None:
        with self._owner._guard:
            self._closed = True


@runtime_checkable
class PreparedOperationProvider(Protocol):
    """Optional capability, separate from the existing tool-provider protocol."""

    def prepare_call(self, tool_name: str, arguments: dict[str, Any], *, ctx: Any = None) -> PreparedOperation: ...
