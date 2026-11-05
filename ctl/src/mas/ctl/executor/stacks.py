#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The session's ``agent_comm`` chain: start it, link each agent on it, close it."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator, Mapping
from typing import Any

from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.env import env_named_secret
from mas.ctl.infra.identities import (
    agent_chains,
    chain_attachments,
    chain_for,
    chain_name,
    has_chain,
)
from mas.library.standard.lib.stack import StackHandle
from mas.library.standard.lib.stack.handle import RunTurnFn, SessionTurn
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute

logger = logging.getLogger(__name__)


class ChainStartError(RuntimeError):
    """The Flavour's chain could not start: a missing secret, an unreachable node, a bad layer param."""


class ChainHandles(Mapping[str, StackHandle]):
    """The chain handles one session opened, by chain name: one ``StackHandle`` per distinct chain.

    Every session owns one from the start, empty until a chain opens. ``close`` closes every
    handle and empties it: it is safe to call twice, and does nothing when no chain was opened.
    A handle that fails to close is logged and the others still close, so ``close`` can run
    in cleanup without hiding the error that ended the session.

    It also keeps one turn lock per agent (``turn_lock``): a served agent answers one request
    at a time, and the user's own turns on that agent take the same lock. A request that
    waits longer than ``turn_wait_s`` (default 120, the same as a link's ``reply_timeout_s``)
    fails instead of hanging.
    """

    def __init__(self, turn_wait_s: float = 120.0) -> None:
        self._handles: dict[str, StackHandle] = {}
        self._turn_locks: dict[str, threading.RLock] = {}
        self._turn_locks_guard = threading.Lock()
        self.turn_wait_s = float(turn_wait_s)

    def turn_lock(self, agent_id: str) -> threading.RLock:
        """The lock every turn of ``agent_id`` in this session holds: the session turn is not reentrant.

        Re-entrant on one thread, so a turn that comes back to its own agent on the same thread
        (an in-process call) still runs as before; a turn from another thread waits up to
        ``turn_wait_s``, then the request fails.
        """
        with self._turn_locks_guard:
            return self._turn_locks.setdefault(str(agent_id), threading.RLock())

    def serialized(self, run_turn: RunTurnFn) -> SessionTurn:
        """``run_turn`` that runs one turn at a time per agent; awaiting it runs the turn on a worker thread."""

        def call(agent_id: str, *args: Any, **kwargs: Any) -> str:
            lock = self.turn_lock(agent_id)
            if not lock.acquire(timeout=self.turn_wait_s):
                raise AgentCommError(f"agent {agent_id!r} did not become free in {self.turn_wait_s:.0f}s")
            try:
                return run_turn(agent_id, *args, **kwargs)
            finally:
                lock.release()

        async def acall(agent_id: str, *args: Any, **kwargs: Any) -> str:
            return await asyncio.to_thread(call, agent_id, *args, **kwargs)

        return SessionTurn(call, acall)

    def __getitem__(self, name: str) -> StackHandle:
        return self._handles[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._handles)

    def __len__(self) -> int:
        return len(self._handles)

    def add(self, name: str, handle: StackHandle) -> None:
        self._handles[name] = handle

    def close(self) -> None:
        handles = list(self._handles.items())
        self._handles.clear()
        for name, handle in handles:
            try:
                handle.close()
            except Exception:
                logger.exception("failed to close stack %s", name)


def _with_secrets(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each row's ``secret_env`` read here, passed to the layer as ``secret``. No layer reads env."""
    out: list[dict[str, Any]] = []
    for layer in chain:
        params = dict(layer.get("params") or {})
        secret_env = str(params.pop("secret_env", "") or "").strip()
        if secret_env:
            try:
                params["secret"] = env_named_secret(secret_env)
            except ValueError as exc:
                raise ChainStartError(f"Application {layer.get('row')!r}: {exc}") from exc
        out.append({"type": layer.get("type"), "params": params})
    return out


def chain_handle(registry: Any, chain: list[dict[str, Any]], *, handles: ChainHandles) -> StackHandle | None:
    """The started handle of one chain, kept in the session's ``handles``; one handle per distinct chain.

    The handle has no turn of its own: only an agent served on the chain is linked with one.
    A chain that cannot start closes every handle the session already opened.
    """
    if not chain:
        return None
    name = chain_name(chain)
    if name in handles:
        return handles[name]
    try:
        handle = StackHandle(name, _with_secrets(list(chain)), registry=registry)
        handle.start()
    except ChainStartError:
        handles.close()
        raise
    except Exception as exc:
        handles.close()
        raise ChainStartError(f"bind.agent_comm {name!r}: {exc}") from exc
    handles.add(name, handle)
    return handle


def chain_links(
    registry: Any,
    resolved: ResolvedInfra,
    *,
    run_turn: RunTurnFn,
    local_ids: list[str],
    handles: ChainHandles,
    peers: dict[str, set[str]],
) -> tuple[dict[str, Any], dict[str, dict[str, AgentCommRoute]]]:
    """Each local agent's links on the Flavour's chains, and the chain routes each sender has.

    An agent served here (``listen``) gets a link on its own chain and is
    reached on it: requests that arrive run its turn, one at a time
    (``ChainHandles.turn_lock``). A sender gets a link on the chain of each
    target it delegates to that is reached over a chain (``connect``), and
    routes that target through it; that link has no turn, so a request that
    reaches it is refused. An agent that neither serves nor sends keeps the
    default in-process link.
    """
    if not has_chain(resolved):
        return {}, {}
    served, used = chain_attachments(resolved)
    serve_turn = handles.serialized(run_turn)
    handlers: dict[str, Any] = {}
    routes_for: dict[str, dict[str, AgentCommRoute]] = {}
    for caller in local_ids:
        routes: dict[str, AgentCommRoute] = {}
        for chain in agent_chains(caller, peers=peers, infra=resolved):
            handle = chain_handle(registry, chain, handles=handles)
            own = caller in served and chain_name(chain) == chain_name(chain_for(resolved, caller))
            try:
                comm = handle.link(caller, run_turn=serve_turn if own else None)
            except Exception as exc:  # the link connects here: an unreachable node, a bad layer param
                handles.close()
                raise ChainStartError(f"bind.agent_comm {chain_name(chain)!r}: agent {caller!r}: {exc}") from exc
            if own:
                handlers[caller] = comm
                routes[caller] = AgentCommRoute(caller, handle.plugin_id, comm)
            handlers.setdefault(caller, comm)
            for target in sorted((peers.get(caller) or set()) & used):
                if chain_name(chain_for(resolved, target)) == chain_name(chain):
                    routes[target] = AgentCommRoute(target, handle.plugin_id, comm)
        if routes:
            routes_for[caller] = routes
    return handlers, routes_for
