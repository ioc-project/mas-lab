#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``usage`` of an infra endpoint row: ``connect``, ``listen``, or both.

Application endpoints and ToolServerRegistry servers say how this process
relates to the endpoint:

* ``connect``: this process is a client of it (CLI ``--connect``).
* ``listen``: this process serves it (CLI ``--listen``).
* ``[connect, listen]``: both.

The old values ``use``, ``deploy`` and ``use-and-deploy`` are deprecated
aliases of ``connect``, ``listen`` and ``[connect, listen]``. They are still
read; :func:`deprecated_usage` names the replacement for warnings.
"""

from __future__ import annotations

from typing import Any

CONNECT = "connect"
LISTEN = "listen"
USAGES = (CONNECT, LISTEN)

#: Deprecated value → the modes it stands for.
DEPRECATED_USAGES: dict[str, tuple[str, ...]] = {
    "use": (CONNECT,),
    "deploy": (LISTEN,),
    "use-and-deploy": (CONNECT, LISTEN),
}

_ERROR = "usage must be 'connect', 'listen', or [connect, listen] (deprecated: 'use', 'deploy', 'use-and-deploy')"


def usage_modes(value: Any, *, default: tuple[str, ...] = (CONNECT,)) -> frozenset[str]:
    """The modes a ``usage`` value stands for. ``None`` gives ``default``.

    Raises ``ValueError`` for anything else than a known value or a non-empty
    list of distinct new values.
    """
    if value is None:
        return frozenset(default)
    if isinstance(value, str):
        if value in USAGES:
            return frozenset((value,))
        if value in DEPRECATED_USAGES:
            return frozenset(DEPRECATED_USAGES[value])
        raise ValueError(_ERROR)
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
        if not items or len(set(items)) != len(items) or any(v not in USAGES for v in items):
            raise ValueError(_ERROR)
        return frozenset(items)
    raise ValueError(_ERROR)


def usage_connects(value: Any, *, default: tuple[str, ...] = (CONNECT,)) -> bool:
    return CONNECT in usage_modes(value, default=default)


def usage_listens(value: Any, *, default: tuple[str, ...] = (CONNECT,)) -> bool:
    return LISTEN in usage_modes(value, default=default)


def deprecated_usage(value: Any) -> str | None:
    """The replacement to suggest when ``value`` is a deprecated alias, else None."""
    if not isinstance(value, str) or value not in DEPRECATED_USAGES:
        return None
    modes = DEPRECATED_USAGES[value]
    return modes[0] if len(modes) == 1 else "[" + ", ".join(modes) + "]"
