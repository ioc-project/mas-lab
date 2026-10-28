#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared checks for an agent_comm link. Pass real endpoints; do not hardcode a plugin."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from mas.library.standard.lib.comm.capabilities import CAPABILITY_FLAGS, capability_set
from mas.library.standard.lib.comm.headers import bound_headers, get_headers, merge_headers
from mas.library.standard.lib.comm.send_context import align_send
from mas.library.standard.plugins.agentcomm.layer import BoundLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext

OnRequestSetter = Callable[[int, Callable[..., str]], None]


class UnicastOnlyLink(BoundLayer):
    """Decorator/test layer that advertises unicast and not group."""

    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(unicast=True)

    def __init__(self, agent_id: str = "", **params: Any) -> None:
        super().__init__(**params)
        self._label = agent_id

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)


class MemoryLink:
    """In-process unicast mesh for the shared checks. Not a product plugin."""

    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(unicast=True)

    def __init__(self, agent_id: str, peers: dict[str, MemoryLink] | None = None) -> None:
        self.agent_id = agent_id
        self._peers = peers if peers is not None else {}
        self._on_request: Callable[..., str] | None = None
        self._starts = 0
        self._stops = 0

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def start(self) -> None:
        self._starts += 1

    def stop(self) -> None:
        self._stops += 1

    def send(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        del correlation_id, caller_call_id, context_id
        peer = self._peers.get(target_agent_id)
        if peer is None:
            raise AgentCommError(f"missing peer {target_agent_id!r}")
        headers, _context = align_send(headers, context, sender=self.agent_id)
        merged = merge_headers(headers)
        handler = peer._on_request
        with bound_headers(merged):
            if handler is None:
                return f"ack:{task}"
            return handler(task)

    async def asend(self, *args: Any, **kwargs: Any) -> str:
        return self.send(*args, **kwargs)


def make_unicast_mesh(names: Sequence[str] = ("a", "b", "c")) -> list[MemoryLink]:
    peers: dict[str, MemoryLink] = {}
    links = [MemoryLink(name, peers) for name in names]
    for link in links:
        peers[link.agent_id] = link
    return links


def check_agent_comm(
    endpoints: Sequence[Any],
    *,
    on_request: OnRequestSetter | None = None,
    ping: str = "ping",
    ack_prefix: str = "ack:",
) -> None:
    """Run send, header, and capability checks. Reply is ``ack_prefix + ping``."""
    if len(endpoints) < 3:
        raise ValueError("check_agent_comm needs at least three endpoints")
    a, b, c = endpoints[0], endpoints[1], endpoints[2]
    if a is b or b is c or a is c:
        raise AssertionError("check_agent_comm needs three distinct endpoints")
    _assert_unicast(a, b, ping=ping, expected=f"{ack_prefix}{ping}")
    if on_request is not None:
        _assert_unicast_headers(a, b, on_request, ack_prefix=ack_prefix)
    caps = a.capabilities()
    missing = [flag for flag in CAPABILITY_FLAGS if flag not in caps]
    if missing:
        raise AssertionError(f"capabilities() omitted {missing}")
    for flag, value in caps.items():
        if flag in CAPABILITY_FLAGS and type(value) is not bool:
            raise AssertionError(f"capability {flag!r} is not a bool")


async def check_agent_comm_async(
    endpoints: Sequence[Any],
    *,
    ping: str = "ping",
    ack_prefix: str = "ack:",
) -> None:
    if len(endpoints) < 2:
        raise ValueError("check_agent_comm_async needs at least two endpoints")
    a, b = endpoints[0], endpoints[1]
    reply = await a.asend(str(b.agent_id), ping)
    expected = f"{ack_prefix}{ping}"
    if reply != expected:
        raise AssertionError(f"asend reply {reply!r}")


def _assert_unicast(a: Any, b: Any, *, ping: str, expected: str) -> None:
    reply = a.send(str(b.agent_id), ping)
    if reply != expected:
        raise AssertionError(f"unicast reply {reply!r}")
    try:
        a.send("missing-peer", "x")
    except AgentCommError:
        return
    raise AssertionError("unicast to a missing peer must raise AgentCommError")


def _assert_subset(expected: dict[str, str], seen: dict[str, str], where: str) -> None:
    missing = {k: v for k, v in expected.items() if seen.get(k) != v}
    if missing:
        raise AssertionError(f"{where}: headers {missing!r} did not arrive (saw {seen!r})")


def _assert_unicast_headers(
    a: Any,
    b: Any,
    on_request: OnRequestSetter,
    *,
    ack_prefix: str,
) -> None:
    seen: list[dict[str, str]] = []

    def _handler(task: str, **_kw: Any) -> str:
        seen.append(get_headers())
        return f"{ack_prefix}{task}"

    on_request(1, _handler)
    explicit = {"x-check": "explicit"}
    if a.send(str(b.agent_id), "h1", headers=explicit) != f"{ack_prefix}h1":
        raise AssertionError("unicast with headers changed the reply")
    _assert_subset(explicit, seen[-1], "unicast explicit")
    hop = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    with bound_headers({"x-ambient": "ambient", "x-check": "ambient", "traceparent": hop}):
        a.send(str(b.agent_id), "h2", headers=explicit)
        arrived = seen[-1]
        _assert_subset({"x-check": "explicit"}, arrived, "unicast merge explicit wins")
        if arrived.get("x-ambient") == "ambient":
            raise AssertionError("a non-hop ambient header rode the send")
        if "traceparent" not in arrived:
            raise AssertionError("hop-safe ambient traceparent did not ride the send")
        if get_headers().get("x-check") != "ambient":
            raise AssertionError("receiver headers leaked into the sender context")
    if "x-ambient" in get_headers():
        raise AssertionError("ambient headers leaked out of their block")
