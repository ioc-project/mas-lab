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

from mas.runtime import hostenv
from mas.runtime.entrypoint import install_host_environment
from mas.runtime.envkeys import LLM_SCHEDULER_KEYS

_OVERRIDES = {
    "MAS_LLM_RPM": ("requests_per_minute", float),
    "MAS_LLM_MAX_IN_FLIGHT": ("max_in_flight", int),
    "MAS_LLM_SCHEDULER_USER": ("user", str),
    "MAS_LLM_SCHEDULER_PROVIDER": ("provider", str),
    "MAS_LLM_SCHEDULER_MODEL": ("model", str),
    "MAS_LLM_SCHEDULER_STATE": ("state_path", str),
}


def scheduler_fingerprint(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The declared scheduler keys and their values. A controller daemon and its client compare them
    to notice a daemon started from another shell. Never a secret, never store material."""
    out: dict[str, str] = {}
    for name in sorted(LLM_SCHEDULER_KEYS):
        value = hostenv.get(name) if environ is None else environ.get(name)
        if value:
            out[name] = str(value)
    return out


def llm_scheduler_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Scheduler override mapping from the environment. Empty values are ignored."""
    source = os.environ if env is None else env
    out: dict[str, Any] = {}
    for name, (key, cast) in _OVERRIDES.items():
        raw = str(source.get(name, "")).strip()
        if raw:
            out[key] = cast(raw)
    return out


def refresh_env_overrides() -> None:
    """Re-read hostenv after a ``.env`` overlay of non-secret ``MAS_LLM_*`` keys.

    Adds to what is installed instead of replacing it: a host may have handed this process its own
    overrides (a spawned run worker), and a ``.env`` read mid-run must not drop them. A variable in
    hostenv wins over an override of the same key. Values never enter ``os.environ``.
    """
    from mas.runtime.engine.llm_cache import install_cli_path
    from mas.runtime.engine.llm_scheduler import explicit_overrides, install_overrides

    source = hostenv.snapshot()
    install_overrides({**explicit_overrides(), **llm_scheduler_overrides(source)})
    cache = str(source.get("MAS_LLM_CACHE", "") or "").strip()
    if cache:
        install_cli_path(cache)


def install_env_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read the variables and install them as the process's scheduler overrides.

    With no *env*, installs the host first and reads hostenv, overlay included: a ``.env`` already
    read keeps its ``MAS_LLM_*`` values.
    """
    from mas.runtime.engine.llm_cache import install_cli_path
    from mas.runtime.engine.llm_scheduler import install_overrides

    source = install_host_environment() if env is None else env
    overrides = llm_scheduler_overrides(source)
    install_overrides(overrides)
    install_cli_path(str(source.get("MAS_LLM_CACHE", "")).strip() or None)
    return overrides
