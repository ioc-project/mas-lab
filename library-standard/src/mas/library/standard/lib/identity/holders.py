#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which wallets an agent holds: the process view the identity gate reads.

The identity layer registers the :class:`~mas.library.standard.lib.identity.caller.Caller` it built for an agent and a
record, and removes it when it stops. A wallet is held weakly, so a layer that is gone does not stay alive here.
Wallets are also kept per *scope*: the token the control plane stamps once per bind, so two runs in one process that
name the same agent and record never read each other's wallet. An empty scope is the unscoped process (standalone
use). Nothing in the table is a credential: the gate asks a wallet for its public view and whether it is revoked.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Mapping
from typing import Any

from mas.library.standard.lib.identity.caller import Caller

_LOCK = threading.Lock()
_HELD: dict[tuple[str, str, str], list[weakref.ref[Caller]]] = {}


#: The reserved agent-spec key the host stamps the scope token under; the gate and the system tool read it there.
SCOPE_KEY = "identity_scope"


def scope_of(spec: Mapping[str, Any] | None) -> str:
    """The scope token of an agent spec (``""`` when the host stamped none)."""
    value = spec.get(SCOPE_KEY) if isinstance(spec, Mapping) else None
    return value if isinstance(value, str) else ""


def register(agent_id: str, record: str, caller: Caller, scope: str = "") -> Callable[[], None]:
    """Record that ``agent_id`` holds ``caller`` as ``record``; return the function that takes it away again."""
    key = (str(scope or ""), str(agent_id or ""), str(record))
    entry = weakref.ref(caller)
    with _LOCK:
        _HELD.setdefault(key, []).append(entry)

    def release() -> None:
        with _LOCK:
            entries = _HELD.get(key)
            if entries is None:
                return
            if entry in entries:
                entries.remove(entry)
            if not entries:
                del _HELD[key]

    return release


def records_of(agent_id: str, scope: str = "") -> list[str]:
    """The records ``agent_id`` holds a live wallet for, in a stable order."""
    agent, scope = str(agent_id or ""), str(scope or "")
    with _LOCK:
        return sorted(
            record for (sc, who, record) in list(_HELD) if sc == scope and who == agent and _live((sc, who, record))
        )


def wallets(agent_id: str, record: str, scope: str = "") -> list[Caller]:
    """The live wallets ``agent_id`` holds for ``record``, each once (layers that share a wallet register it twice)."""
    with _LOCK:
        return _live((str(scope or ""), str(agent_id or ""), str(record)))


def _live(key: tuple[str, str, str]) -> list[Caller]:
    """The live, distinct wallets under ``key``; dead references are dropped, and so is a key left with none."""
    entries = [entry for entry in _HELD.get(key, []) if entry() is not None]
    if entries:
        _HELD[key] = entries
    else:
        _HELD.pop(key, None)
    distinct: dict[int, Caller] = {}
    for entry in entries:
        held = entry()
        if held is not None:
            distinct.setdefault(id(held), held)
    return list(distinct.values())


def reset() -> None:
    """Forget every holder (tests)."""
    with _LOCK:
        _HELD.clear()
