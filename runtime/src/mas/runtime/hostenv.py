#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The environment a host hands to library and runtime code.

Library and runtime code never touches ``os.environ``. The CLI entry points
(``mas-ctl``, ``mas-lab``) call :func:`install` once with their environment; code
here reads :func:`get`. Values other code exports for a run (a service's ``env:``
values) go through :func:`overlay`, never into the process environment.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

_LOCK = threading.Lock()
_BASE: Mapping[str, str] = {}
_OVERLAY: dict[str, str] = {}


def install(env: Mapping[str, str] | None) -> None:
    """Install the host's environment (a snapshot, or a live mapping in tests)."""
    global _BASE
    _BASE = {} if env is None else (env if not isinstance(env, dict) else dict(env))


def get(name: str, default: str | None = None) -> str | None:
    with _LOCK:
        if name in _OVERLAY:
            return _OVERLAY[name]
    value = _BASE.get(name)
    return default if value is None else value


def snapshot() -> dict[str, str]:
    """Base environment plus overlay, for a child process."""
    with _LOCK:
        return {**dict(_BASE), **_OVERLAY}


def overlay_snapshot() -> dict[str, str]:
    """Only the values exported for a run (no base): what a spawned worker cannot inherit."""
    with _LOCK:
        return dict(_OVERLAY)


def set_overlay(values: Mapping[str, str]) -> None:
    with _LOCK:
        _OVERLAY.update({str(k): str(v) for k, v in values.items()})


def clear_overlay(names: list[str] | tuple[str, ...]) -> None:
    with _LOCK:
        for name in names:
            _OVERLAY.pop(name, None)


@contextmanager
def overlay(values: Mapping[str, str]) -> Iterator[None]:
    with _LOCK:
        previous = {k: _OVERLAY.get(k) for k in values}
        _OVERLAY.update({str(k): str(v) for k, v in values.items()})
    try:
        yield
    finally:
        with _LOCK:
            for k, v in previous.items():
                if v is None:
                    _OVERLAY.pop(k, None)
                else:
                    _OVERLAY[k] = v
