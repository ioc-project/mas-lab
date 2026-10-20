#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared resolution of pipeline-relative paths and dependency outputs."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence, overload

logger = logging.getLogger(__name__)


def pipeline_config_dir(ctx: Any) -> Optional[Path]:
    pipeline = getattr(ctx, "pipeline", None)
    config_path = getattr(pipeline, "config_path", None) if pipeline is not None else None
    return config_path.parent if config_path is not None else None


def resolve_pipeline_relative(ctx: Any, value: str | Path) -> Path:
    """Resolve a path against the pipeline YAML directory, then the cwd."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    base = pipeline_config_dir(ctx)
    if base is not None:
        candidate = (base / path).resolve()
        if candidate.exists():
            return candidate
    return (Path.cwd() / path).resolve()


@overload
def path_from_config_or_deps(
    step: Any,
    ctx: Any,
    config: Mapping[str, Any],
    config_keys: Sequence[str],
    dep_keys: Sequence[str],
    *,
    required: Literal[True] = True,
) -> Path: ...


@overload
def path_from_config_or_deps(
    step: Any,
    ctx: Any,
    config: Mapping[str, Any],
    config_keys: Sequence[str],
    dep_keys: Sequence[str],
    *,
    required: Literal[False],
) -> Optional[Path]: ...


def path_from_config_or_deps(
    step: Any,
    ctx: Any,
    config: Mapping[str, Any],
    config_keys: Sequence[str],
    dep_keys: Sequence[str],
    *,
    required: bool = True,
) -> Optional[Path]:
    """Prefer an explicit config path, then the first matching dependency output."""
    raw: Any = None
    for key in config_keys:
        cand = config.get(key)
        if cand:
            raw = cand
            break
    if not raw:
        for dep_name in getattr(step, "depends_on", None) or ():
            dep_out = ctx.step_outputs.get(dep_name)
            if dep_out is None:
                continue
            data = getattr(dep_out, "data", None) or {}
            for key in dep_keys:
                if data.get(key):
                    raw = data[key]
                    logger.info(
                        "Step '%s': using %s from dependency '%s': %s",
                        getattr(step, "name", step),
                        key,
                        dep_name,
                        raw,
                    )
                    break
            if raw:
                break
    if not raw:
        if required:
            cfg = "' / '".join(config_keys) or "path"
            deps = "' / '".join(dep_keys) or "dependency output"
            raise ValueError(
                f"Step '{getattr(step, 'name', step)}': no {cfg} in config "
                f"and no '{deps}' found in dependency outputs."
            )
        return None
    return resolve_pipeline_relative(ctx, str(raw))
