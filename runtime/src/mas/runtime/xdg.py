#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""XDG Base Directory paths for MAS (config, data, cache, state)."""

from __future__ import annotations

from pathlib import Path

from mas.runtime import hostenv
from mas.runtime.constants import USER_CONFIG_FILENAME

_APP = "mas"

USER_CONFIG_SOURCE = "$XDG_CONFIG_HOME/mas/config.yaml"
DEFAULT_PATH_SOURCE = "default (XDG)"
LABS_DIR_SOURCE = "$XDG_DATA_HOME/mas/labs"
RUNS_DIR_SOURCE = "$XDG_DATA_HOME/mas/runs"
TRACE_CACHE_SOURCE = "$XDG_CACHE_HOME/mas/traces"
ARTIFACTS_CACHE_SOURCE = "$XDG_CACHE_HOME/mas/artifacts"
LLM_CACHE_SOURCE = "$XDG_CACHE_HOME/mas/llm_cache.json"
LAST_RUN_SOURCE = "$XDG_STATE_HOME/mas/last-run.json"
AGENT_SESSIONS_SOURCE = "$XDG_DATA_HOME/mas/agents/{agent_id}/sessions/"
AGENT_MEMORY_SOURCE = "$XDG_DATA_HOME/mas/memory/{agent_id}.sqlite"
MAS_HOME_SOURCE = "$XDG_DATA_HOME/mas"
CONTROLLER_SOCKET_SOURCE = "$XDG_DATA_HOME/mas/controller.sock"


def _env_path(name: str, default: Path) -> Path:
    raw = hostenv.get(name, "").strip()
    if not raw:
        return default
    return Path(raw).expanduser().resolve()


def xdg_config_home() -> Path:
    return _env_path("XDG_CONFIG_HOME", Path.home() / ".config")


def xdg_data_home() -> Path:
    return _env_path("XDG_DATA_HOME", Path.home() / ".local" / "share")


def xdg_cache_home() -> Path:
    return _env_path("XDG_CACHE_HOME", Path.home() / ".cache")


def xdg_state_home() -> Path:
    return _env_path("XDG_STATE_HOME", Path.home() / ".local" / "state")


def mas_config_dir() -> Path:
    return xdg_config_home() / _APP


def mas_user_config_file() -> Path:
    return mas_config_dir() / USER_CONFIG_FILENAME


def mas_data_root() -> Path:
    """Persistent application data (labs, runs, scratch data)."""
    return xdg_data_home() / _APP


def mas_cache_root() -> Path:
    return xdg_cache_home() / _APP


def mas_state_root() -> Path:
    return xdg_state_home() / _APP


def mas_infra_dir() -> Path:
    return mas_config_dir() / "infra"


def mas_runtime_dir() -> Path:
    """User-level RuntimeEngine manifests (parallel to ``mas_infra_dir()``)."""
    return mas_config_dir() / "runtime"
