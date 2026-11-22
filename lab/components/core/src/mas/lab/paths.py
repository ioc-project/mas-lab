#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical data path resolution for mas-lab.

All CLI commands resolve storage locations through the same precedence ladder
(see :func:`resolve_path`).  Defaults follow the `XDG Base Directory Specification`_.

.. _XDG Base Directory Specification: https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html

Precedence (highest first)
--------------------------
1. Explicit CLI argument (per command)
2. ``MAS_*`` environment variables
3. Project ``config.yaml`` ``paths:`` block
4. ``$XDG_CONFIG_HOME/mas/config.yaml`` top-level ``labs_dir`` / ``cache_dir`` / ``runs_dir``
5. XDG defaults under ``$XDG_DATA_HOME/mas/``, ``$XDG_CACHE_HOME/mas/``

Trace and pipeline caches honour ``MAS_DATA_ROOT`` / ``MAS_LAB_DATA`` via
:func:`data_dir` when ``cache_dir`` is not overridden. Otherwise traces default
to ``$XDG_CACHE_HOME/mas/traces`` (or ``<cache_dir>/traces`` when configured).

Relative path values in any loaded config file resolve from that file's
directory (same rule for project ``config.yaml`` and user config).

Benchmark output layouts (both indexed by ``benchmark list``)
-------------------------------------------------------------
* **Nested** — legacy single-agent runs create timestamped directories::

      <labs_root>/2026-07-01_12-30-00_a1b2c3d4/metadata.yaml

* **Flat** — MAS batch runs with a fixed ``-o`` / ``output_dir`` write one
  ``metadata.yaml`` at the output root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mas.runtime import hostenv
from mas.runtime.constants import LAB_CONFIG_FILENAME, WORKSPACE_CONFIG_FILENAME
from mas.runtime.workspace_config import (
    RuntimeWorkspaceConfig,
    _user_config_path,
    find_workspace_file,
    resolve_config_relative,
)
from mas.runtime.xdg import (
    DEFAULT_PATH_SOURCE,
    USER_CONFIG_SOURCE,
    mas_cache_root,
    mas_data_root,
)

# -----------------------------------------------------------------
# Environment-variable names (public — used by `mas-lab config`)
# -----------------------------------------------------------------
MAS_DATA_ROOT_ENV = "MAS_DATA_ROOT"
MAS_LABS_ROOT_ENV = "MAS_LABS_ROOT"
MAS_LAB_DATA_ENV = "MAS_LAB_DATA"
MAS_TRACE_CACHE_ENV = "MAS_TRACE_CACHE"
MAS_DATA_CACHE_ENV = "MAS_DATA_CACHE"
MAS_RUNS_ROOT_ENV = "MAS_RUNS_ROOT"
MAS_STANDALONE_RUNS_ENV = "MAS_STANDALONE_RUNS_ROOT"

PathKey = Literal["labs_dir", "cache_dir", "runs_dir"]

_ENV_FOR_KEY: dict[PathKey, str | None] = {
    "labs_dir": MAS_LABS_ROOT_ENV,
    "cache_dir": None,
    "runs_dir": MAS_RUNS_ROOT_ENV,
}


@dataclass(frozen=True)
class ResolvedPath:
    """A filesystem path plus a short tag describing where it came from."""

    path: Path
    source: str


def _load_user_config_yaml() -> dict:
    """Load user config from ``$XDG_CONFIG_HOME/mas/config.yaml``."""
    cfg_path = _user_config_path()
    if not cfg_path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _raw_from_user_config(key: PathKey) -> str | None:
    raw = _load_user_config_yaml()
    value = raw.get(key)
    if value:
        return str(value)
    paths_block = raw.get("paths")
    if isinstance(paths_block, dict) and paths_block.get(key):
        return str(paths_block[key])
    return None


def _raw_from_workspace(key: PathKey) -> tuple[str | None, Path | None]:
    ws = RuntimeWorkspaceConfig.load()
    if not ws.found:
        return None, None
    value = ws.paths.get(key)
    if not value:
        return None, None
    return value, ws.root


def _default_for(key: PathKey) -> Path:
    data = mas_data_root()
    cache = mas_cache_root()
    if key == "labs_dir":
        return data / "labs"
    if key == "cache_dir":
        return cache
    if key == "runs_dir":
        return data / "runs"
    raise ValueError(key)


def resolve_path(
    key: PathKey,
    *,
    explicit: Path | str | None = None,
    env_var: str | None = None,
) -> ResolvedPath:
    """Resolve *key* using the unified config ladder."""
    if explicit:
        return ResolvedPath(Path(str(explicit)).expanduser().resolve(), "cli")

    _env = env_var or _ENV_FOR_KEY.get(key)
    if _env:
        _raw_env = hostenv.get(_env, "").strip()
        if _raw_env:
            return ResolvedPath(Path(_raw_env).expanduser().resolve(), f"${_env}")

    ws_raw, ws_root = _raw_from_workspace(key)
    if ws_raw and ws_root is not None:
        cfg_path = find_workspace_file()
        if cfg_path is None:
            p = Path(ws_raw).expanduser().resolve()
        else:
            p = resolve_config_relative(ws_raw, cfg_path)
        name = cfg_path.name if cfg_path else WORKSPACE_CONFIG_FILENAME
        return ResolvedPath(p, name)

    user_raw = _raw_from_user_config(key)
    if user_raw:
        cfg_path = _user_config_path()
        if cfg_path.is_file():
            p = resolve_config_relative(user_raw, cfg_path)
        else:
            p = Path(user_raw).expanduser().resolve()
        return ResolvedPath(p, USER_CONFIG_SOURCE)

    return ResolvedPath(_default_for(key), DEFAULT_PATH_SOURCE)


def data_root() -> Path:
    """Return the root directory for all mas-lab persistent data."""
    _raw = hostenv.get(MAS_DATA_ROOT_ENV, "").strip()
    if _raw:
        return Path(_raw).expanduser()
    labs = resolve_path("labs_dir")
    if labs.source != DEFAULT_PATH_SOURCE:
        return labs.path.parent
    return mas_data_root()


def labs_root() -> Path:
    """Return the root directory for benchmark / experiment outputs."""
    return resolve_path("labs_dir").path


def benchmark_root() -> Path:
    """Alias for :func:`labs_root` (benchmarks are experiments in labs)."""
    return labs_root()


def data_dir() -> Path:
    """Return the directory for ephemeral / support data (trace cache, scratch)."""
    _raw = hostenv.get(MAS_LAB_DATA_ENV, "").strip()
    if _raw:
        return Path(_raw).expanduser()
    _raw_data = hostenv.get(MAS_DATA_ROOT_ENV, "").strip()
    if _raw_data:
        return Path(_raw_data).expanduser() / "data"
    return mas_data_root() / "data"


def lab_output() -> Path:
    """Return the directory for interactive demo / lab-config outputs."""
    return data_dir() / "lab-output"


def trace_cache(explicit: str | None = None) -> Path:
    """Return the directory used for run trace caching."""
    if explicit:
        return Path(explicit).expanduser()
    _raw = hostenv.get(MAS_TRACE_CACHE_ENV, "").strip()
    if _raw:
        return Path(_raw).expanduser()
    cache = resolve_path("cache_dir")
    if cache.source != DEFAULT_PATH_SOURCE:
        return cache.path / "traces"
    if hostenv.get(MAS_DATA_ROOT_ENV) or hostenv.get(MAS_LAB_DATA_ENV):
        return data_dir() / "trace-cache"
    return mas_cache_root() / "traces"


def _pipeline_cache_under_data_dir(data: Path) -> Path:
    """Pipeline step cache under ``data_dir`` (``cache/``)."""
    return data / "cache"


def data_cache(explicit: str | None = None) -> Path:
    """Return the directory used for pipeline step caching."""
    if explicit:
        return Path(explicit).expanduser()
    _raw = hostenv.get(MAS_DATA_CACHE_ENV, "").strip()
    if _raw:
        return Path(_raw).expanduser()
    cache = resolve_path("cache_dir")
    if cache.source != DEFAULT_PATH_SOURCE:
        return cache.path / "artifacts"
    if hostenv.get(MAS_DATA_ROOT_ENV) or hostenv.get(MAS_LAB_DATA_ENV):
        return _pipeline_cache_under_data_dir(data_dir())
    return mas_cache_root() / "artifacts"


def runs_root() -> Path:
    """Return the directory where agent run artefacts are stored."""
    return resolve_path("runs_dir").path


def standalone_runs_root() -> Path:
    """Return the directory where standalone run records are stored."""
    _raw = hostenv.get(MAS_STANDALONE_RUNS_ENV, "").strip()
    return Path(_raw).expanduser() if _raw else data_dir() / "standalone-runs"


def benchmark_search_roots(*, extra: Path | None = None) -> list[Path]:
    """Return ordered roots to scan for ``metadata.yaml`` (benchmark list/show)."""
    roots: list[Path] = []
    for candidate in (labs_root(), runs_root(), extra):
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def path_resolution_summary() -> dict[str, ResolvedPath]:
    """Effective paths for ``mas-lab config`` display."""
    return {
        "labs_dir": resolve_path("labs_dir"),
        "cache_dir": resolve_path("cache_dir"),
        "runs_dir": resolve_path("runs_dir"),
        "trace_cache": ResolvedPath(trace_cache(), _trace_cache_source()),
        "data_dir": ResolvedPath(data_dir(), _data_dir_source()),
        "data_root": ResolvedPath(data_root(), _data_root_source()),
    }


def _trace_cache_source() -> str:
    if hostenv.get(MAS_TRACE_CACHE_ENV):
        return f"${MAS_TRACE_CACHE_ENV}"
    cache = resolve_path("cache_dir")
    if cache.source != DEFAULT_PATH_SOURCE:
        return f"{cache.source} / traces"
    if hostenv.get(MAS_DATA_ROOT_ENV) or hostenv.get(MAS_LAB_DATA_ENV):
        return _data_dir_source() + " / trace-cache"
    return "$XDG_CACHE_HOME/mas/traces"


def _data_dir_source() -> str:
    if hostenv.get(MAS_LAB_DATA_ENV):
        return f"${MAS_LAB_DATA_ENV}"
    if hostenv.get(MAS_DATA_ROOT_ENV):
        return f"${MAS_DATA_ROOT_ENV}/data"
    return "$XDG_DATA_HOME/mas/data"


def _data_root_source() -> str:
    if hostenv.get(MAS_DATA_ROOT_ENV):
        return f"${MAS_DATA_ROOT_ENV}"
    labs = resolve_path("labs_dir")
    if labs.source != DEFAULT_PATH_SOURCE:
        return f"{labs.source} (parent)"
    return "$XDG_DATA_HOME/mas"


def source_tag(
    *,
    key: PathKey = "labs_dir",
    specific_env: str | None = None,
    lab_config: bool = False,
) -> str:
    """Return a short human-readable string describing the path source."""
    if lab_config:
        return LAB_CONFIG_FILENAME
    if specific_env and hostenv.get(specific_env):
        return f"${specific_env}"
    return resolve_path(key).source


def resolve_run_artifact(
    shorthand: str,
    *,
    artifact: str = "events.jsonl",
) -> Path:
    """Resolve a lab-shorthand into an artifact path."""
    parts = [p for p in shorthand.strip("/").split("/") if p]
    root = labs_root()
    if not root.exists():
        raise FileNotFoundError(f"Labs root does not exist: {root}")

    cur = root
    for part in parts:
        candidate = cur / part
        if not candidate.is_dir():
            raise FileNotFoundError(
                f"Directory not found: {candidate}\n"
                f"Available: {', '.join(sorted(d.name for d in cur.iterdir() if d.is_dir())) or '(empty)'}"
            )
        cur = candidate

    MAX_DEPTH = 5
    for _ in range(MAX_DEPTH):
        if _has_artifact(cur, artifact):
            break
        children = sorted(d for d in cur.iterdir() if d.is_dir())
        if not children:
            break
        cur = children[0]

    return _artifact_path(cur, artifact)


def _has_artifact(directory: Path, artifact: str) -> bool:
    if artifact == "events.jsonl":
        return (directory / "traces" / "events.jsonl").exists()
    return (directory / artifact).exists()


def _artifact_path(directory: Path, artifact: str) -> Path:
    if artifact == "events.jsonl":
        p = directory / "traces" / "events.jsonl"
    else:
        p = directory / artifact
    if not p.exists():
        raise FileNotFoundError(
            f"Artifact not found: {p}\n"
            f"Contents of {directory}: "
            f"{', '.join(sorted(f.name for f in directory.iterdir())) if directory.exists() else '(missing)'}"
        )
    return p


def list_labs() -> list[str]:
    """Return the names of all lab directories under :func:`labs_root`."""
    root = labs_root()
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def workspace_relative_path(path: str | Path, *, start: Path | None = None) -> str:
    """Return *path* relative to the workspace root when possible."""
    if not path:
        return ""
    raw = str(path).strip()
    if not raw:
        return ""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        return raw.replace("\\", "/")
    try:
        from mas.lab.workspace import find_workspace_root

        ws = find_workspace_root(start or p)
        if ws is not None:
            return str(p.resolve().relative_to(ws.resolve())).replace("\\", "/")
    except Exception:
        pass
    return raw.replace("\\", "/")
