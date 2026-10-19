#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Discover demo-ui shell modules and registered plot kinds.

Demo-ui has no ``src/plugins`` tree. Chrome and the plot host live next to
``shell.js``. Plot *kinds* come from ``mas.lab.ui.plots`` providers.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_META = re.compile(
    r"export const pluginMeta\s*=\s*(\{.*?\}|\[.*?\])\s*;",
    re.S,
)

_SHELL = ("plot-host.js", "chrome.js", "player.js", "layout-picker.js", "notes-overlay.js")


def demo_ui_src() -> Path:
    env = os.environ.get("MAS_DEMO_UI_ROOT")
    if env:
        cand = Path(env).expanduser() / "src"
        if cand.is_dir():
            return cand
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "demo-ui" / "src"
        if cand.is_dir():
            return cand
    return Path.cwd() / "demo-ui" / "src"


def plugins_dir() -> Path:
    """Deprecated alias: there is no plugins folder. Returns ``demo-ui/src``."""
    return demo_ui_src()


def _parse_meta(text: str) -> list[dict[str, Any]]:
    match = _META.search(text)
    if not match:
        return []
    blob = match.group(1)
    blob = re.sub(r"([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', blob)
    blob = blob.replace("'", '"')
    blob = re.sub(r",(\s*[}\]])", r"\1", blob)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _shell_rows() -> list[dict[str, Any]]:
    root = demo_ui_src()
    rows: list[dict[str, Any]] = []
    for name in _SHELL:
        path = root / name
        if not path.is_file():
            continue
        module = f"/src/{name}"
        metas = _parse_meta(path.read_text(encoding="utf-8"))
        if not metas:
            rows.append({"id": path.stem, "module": module, "kind": "chrome"})
            continue
        for meta in metas:
            row = dict(meta)
            row.setdefault("id", path.stem)
            row["module"] = module
            rows.append(row)
    return rows


def _plot_kind_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        from mas.lab.plots.providers import load_plot_providers

        for provider in load_plot_providers():
            for kind in sorted(provider.kinds):
                stream = ""
                try:
                    stream = provider.stream_for(kind)
                except Exception:
                    stream = ""
                rows.append(
                    {
                        "id": kind,
                        "kind": "plot",
                        "formats": ["html"],
                        "realtime": True,
                        "stream": stream,
                        "module": "/src/plot-host.js",
                    }
                )
    except Exception:
        pass
    return rows


def list_plugins(
    *,
    kind: str | None = None,
    format: str | None = None,
    realtime: bool | None = None,
) -> list[dict[str, Any]]:
    rows = _shell_rows() + _plot_kind_rows()
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("id") or ""), str(row.get("module") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    out = unique
    if kind:
        out = [row for row in out if str(row.get("kind") or "") == kind]
    if format:
        out = [
            row
            for row in out
            if format in [str(x) for x in (row.get("formats") or [])]
            or str(row.get("format") or "") == format
        ]
    if realtime is True:
        out = [row for row in out if row.get("realtime") is True]
    if realtime is False:
        out = [row for row in out if not row.get("realtime")]
    return out
