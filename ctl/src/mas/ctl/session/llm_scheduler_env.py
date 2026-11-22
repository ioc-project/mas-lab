#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Interactive ``MAS_LLM_*`` scheduler and cache-path overrides. The runtime does not read env.

The CLI entry points (``mas-ctl``, ``mas-lab``) call :func:`install_env_overrides`
once; the values reach the ``llm_scheduler`` store as explicit overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

_FLOAT = {"MAS_LLM_RPM": "requests_per_minute"}
_INT = {"MAS_LLM_MAX_IN_FLIGHT": "max_in_flight"}
_TEXT = {
    "MAS_LLM_SCHEDULER_USER": "user",
    "MAS_LLM_SCHEDULER_PROVIDER": "provider",
    "MAS_LLM_SCHEDULER_MODEL": "model",
    "MAS_LLM_SCHEDULER_STATE": "state_path",
}


def llm_scheduler_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Scheduler override mapping from the environment. Empty values are ignored."""
    source = os.environ if env is None else env
    out: dict[str, Any] = {}
    for name, key in _FLOAT.items():
        raw = str(source.get(name, "")).strip()
        if raw:
            out[key] = float(raw)
    for name, key in _INT.items():
        raw = str(source.get(name, "")).strip()
        if raw:
            out[key] = int(raw)
    for name, key in _TEXT.items():
        raw = str(source.get(name, "")).strip()
        if raw:
            out[key] = raw
    return out


def refresh_env_overrides() -> None:
    """Re-read the process environment after a ``.env`` file added to it.

    Adds to what is installed instead of replacing it: a host may have handed this process its own
    overrides (a spawned run worker), and a ``.env`` read mid-run must not drop them. A variable in
    the environment wins over an override of the same key.
    """
    from mas.runtime import hostenv
    from mas.runtime.engine.llm_cache import install_cli_path
    from mas.runtime.engine.llm_scheduler import explicit_overrides, install_overrides

    hostenv.install(dict(os.environ))
    install_overrides({**explicit_overrides(), **llm_scheduler_overrides(os.environ)})
    cache = str(os.environ.get("MAS_LLM_CACHE", "")).strip()
    if cache:
        install_cli_path(cache)


def install_env_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read the variables and install them as the process's scheduler overrides."""
    from mas.runtime import hostenv
    from mas.runtime.engine.llm_cache import install_cli_path
    from mas.runtime.engine.llm_scheduler import install_overrides

    source = os.environ if env is None else env
    if env is None:
        hostenv.install(dict(source))  # the one place a host environment enters library code
    overrides = llm_scheduler_overrides(source)
    install_overrides(overrides)
    install_cli_path(str(source.get("MAS_LLM_CACHE", "")).strip() or None)
    return overrides
