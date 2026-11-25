#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What lets the wallet's changing tools run: a permit the identity guard leaves for one call it let through.

``identity_delegate`` and ``identity_revoke`` change what an agent can do, so the tool refuses to run unless an
``identity-guard`` of the same agent allowed THIS call (or a human approved the hold on it). With no guard configured
no permit is ever left and the tools stay shut: the protection does not depend on an operator remembering to add the
plugin. A permit belongs to one ``(scope, agent, tool, call)``, where ``call`` is the kernel's correlation id of the
tool call; it is used once and lapses after :data:`TTL_S` seconds, so a permit of one call opens nothing for another.
"""

from __future__ import annotations

import threading
import time

#: Seconds a permit stays valid after the guard left it.
TTL_S = 300.0
#: The tools that need a permit.
PERMIT_TOOLS = ("identity_delegate", "identity_revoke")

_LOCK = threading.Lock()
_PERMITS: dict[tuple[str, str, str, str], float] = {}


def grant(scope: str, agent: str, tool: str, call: object, *, clock=time.monotonic) -> None:
    """The guard let call ``call`` of ``tool`` through for ``agent``."""
    now = clock()
    with _LOCK:
        for key in [k for k, at in _PERMITS.items() if now - at >= TTL_S]:
            del _PERMITS[key]
        _PERMITS[_key(scope, agent, tool, call)] = now


def take(scope: str, agent: str, tool: str, call: object, *, clock=time.monotonic) -> bool:
    """Use the permit of this call; ``False`` when no guard of this agent left one for it (or it was used)."""
    with _LOCK:
        at = _PERMITS.pop(_key(scope, agent, tool, call), None)
    return at is not None and clock() - at < TTL_S


def _key(scope: str, agent: str, tool: str, call: object) -> tuple[str, str, str, str]:
    return (str(scope or ""), str(agent or ""), tool, str(call or ""))


def reset() -> None:
    """Forget every permit (tests)."""
    with _LOCK:
        _PERMITS.clear()
