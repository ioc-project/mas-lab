#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A session hook: what a layer contributes to a protocol's session setup, without the protocol knowing why.

Some protocols open a session before the first call (an MCP ``initialize``, an A2A task that answers with an
auth-required state). A layer that must take part in that setup hands the protocol layer a
:class:`SessionHook`. The protocol layer calls it at these points and knows nothing else about it:

1. ``begin(sections, task)``, before the session opens: the capabilities to advertise to the peer, keyed by the
   section names the protocol layer supports (``sections``);
2. ``established(session)``, once the peer has answered: the session object
   shows what the peer declared (``peer``) and can send one more request inside the session (``request``).
   The hook returns the protocol-level fields (``_meta``) to attach to the later calls of this session;
3. ``scope()``, on every call: a session is reused only by calls with the same scope, so what was settled
   for one caller never serves another;
4. ``take_refusal(task)``, when a call fails: the refusal the layer raised below the protocol for that call, if any;
5. ``closed(refusal, task)``, when the protocol layer refuses a call without contacting the peer because the peer
   refused the session setup before: the refusal to raise for this call, in the call's own words;
6. ``on_invalidate(callback)``: the protocol layer registers what to do when what the hook settled stops being
   valid (an identity was revoked): close the held session, the open channel. The layer that owns the hook calls
   ``invalidate()``. Callbacks run on the calling thread, so a protocol layer hops to its own loop.

How a lower layer reaches the protocol layer above it depends on the stack: a call stack (``llm``, ``tool``)
reads ``LayerLifecycle.session_hooks()`` of the layer below when it is built; a send stack (``agent_comm``)
hands the hook down with the envelope, in ``Envelope.meta[SESSION_HOOK_META]``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import Any

#: The session the code running now is making or using, for a hook to tell two sessions' refusals apart: the protocol
#: layer sets it around opening a session (so the tasks that run the session inherit it) and around a call that uses
#: one. ``None`` outside both.
SESSION_TOKEN: ContextVar[object | None] = ContextVar("mas_session_token", default=None)

#: ``Envelope.meta`` key under which a layer hands a :class:`SessionHook` to the protocol layer below it.
SESSION_HOOK_META = "session_hook"


class SessionInfo(ABC):
    """What a hook sees of an open session."""

    #: What the peer declared when the session opened (the MCP ``initialize`` result, the challenge of an A2A task).
    peer: Mapping[str, Any]

    @abstractmethod
    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        """Send ``method`` with ``params`` inside the session and return the peer's answer as a mapping.

        ``data_key`` picks the key of the data part on a message channel (a task); a JSON-RPC session ignores it.
        """


class SessionHook:
    """Base of a session hook. Every method has a default that does nothing."""

    #: Registered by :meth:`on_invalidate` (an immutable default, so a subclass need not call ``super().__init__``).
    _listeners: tuple[Callable[[], None], ...] = ()

    async def begin(self, sections: frozenset[str], task: str) -> Mapping[str, Any]:
        """Capabilities to advertise before the first request, as ``{section name: declaration}``.

        ``sections`` are the section names the protocol layer can carry; a hook asks for no other.
        """
        return {}

    async def established(self, session: SessionInfo) -> Mapping[str, Any]:
        """Settle the session; return the ``_meta`` fields for its later calls (empty for none)."""
        return {}

    def scope(self) -> str:
        """Calls share a session only when their scope is equal. Empty: one scope for every call."""
        return ""

    def take_refusal(self, task: str = "") -> BaseException | None:
        """The refusal this hook's layer raised for a call of the current scope and ``task``, once, else ``None``.

        ``task`` is the operation the call made (an MCP method, ``tools/call:<tool>``): a refusal is kept under it, so
        overlapping calls of different operations each get their own.

        A refusal raised below the protocol (an HTTP auth that refuses a request) surfaces as a closed connection in
        the protocol's client; the client asks the hook what really happened and raises that instead.
        """
        return None

    def closed(self, refusal: BaseException, task: str = "") -> BaseException:
        """The refusal to give a call of ``task`` that fails at once because the peer refused the session before.

        ``refusal`` is the one the peer's refusal produced. A hook whose layer names the incident in its refusals
        (the trace and the call) says it again for this call, with this call's own ids, and records that this call was
        refused. The default gives ``refusal`` back unchanged.
        """
        return refusal

    def on_invalidate(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register ``callback`` for :meth:`invalidate`; the returned function unregisters it."""
        self._listeners = (*self._listeners, callback)

        def forget() -> None:
            self._listeners = tuple(item for item in self._listeners if item is not callback)

        return forget

    def invalidate(self) -> None:
        """What this hook settled is no longer valid: tell every registered protocol layer, once each."""
        for callback in self._listeners:
            callback()


__all__ = ["SESSION_HOOK_META", "SESSION_TOKEN", "SessionHook", "SessionInfo"]
