#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared helpers for ``mas-lab plot`` commands."""
from __future__ import annotations

from pathlib import Path


def resolve_source(source: str) -> str:
    """Resolve a SOURCE argument into an actual file path."""
    p = Path(source).expanduser()
    if p.exists():
        return str(p)

    if "/" in source and not source.endswith((".jsonl", ".json")):
        try:
            from mas.lab import paths as _paths

            resolved = _paths.resolve_run_artifact(source)
            return str(resolved)
        except FileNotFoundError:
            pass

    return source


def default_output(resolved_source: str, plot_kind: str, fmt: str) -> Path:
    """Derive a default output path next to the source file."""
    ext_map = {
        "mermaid": ".mmd", "table": ".txt", "html": ".html",
        "svg": ".svg",
    }
    ext = ext_map.get(fmt, f".{fmt}")
    src = Path(resolved_source).expanduser()
    parent = src.parent
    if parent.name == "traces":
        parent = parent.parent
    return parent / f"{plot_kind}{ext}"
