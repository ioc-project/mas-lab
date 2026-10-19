#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared live-plot HTML shell. Draw functions are injected by each provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TEMPLATE = Path(__file__).parent / "assets" / "live.html"
_D3 = Path(__file__).parent / "assets" / "d3.min.js"

LIVE_FORMATS = frozenset({"html", "htm"})
FILE_FORMATS = frozenset({"svg", "png"})


def d3_inline() -> str:
    if not _D3.is_file():
        raise FileNotFoundError(f"vendored d3 missing: {_D3}")
    return _D3.read_text(encoding="utf-8")


def chart_payload(kind: str, data: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    body = dict(data or {})
    body.setdefault("kind", kind)
    if kind not in {"chat", "topology"}:
        body.setdefault("xrange", 64)
        body.setdefault("yrange", [0.0, 1.0])
    return {
        "kind": kind,
        "title": title or kind.replace("_", " ").title(),
        "xrange": body.get("xrange"),
        "yrange": body.get("yrange"),
        "labels": body.get("labels") or {},
        "xlabel": body.get("xlabel") or "",
        "ylabel": body.get("ylabel") or "",
        "vertical": body.get("vertical"),
        "view": body.get("view"),
        "data": body,
    }


def _json_for_script(value: Any) -> str:
    """JSON that is safe to embed inside a ``<script>`` tag."""
    return json.dumps(value, ensure_ascii=False, default=str).replace("<", "\\u003c")


def render_live(
    kind: str,
    data: dict[str, Any],
    *,
    draw_js: str,
    extra_css: str = "",
    title: str = "",
    fmt: str = "html",
) -> str:
    """Fill the shared live shell. ``fmt`` must be html (callers handle svg/png)."""
    fmt = str(fmt or "html").lower()
    payload = chart_payload(kind, data, title=title)
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, default=str)
    if fmt not in LIVE_FORMATS:
        raise ValueError(f"plot format must be html, svg, or png (got {fmt!r})")
    tmpl = _TEMPLATE.read_text(encoding="utf-8")
    title_text = str(payload["title"]).replace("<", "").replace(">", "")
    return (
        tmpl.replace("{title}", title_text)
        .replace("{extra_css}", extra_css or "")
        .replace("{chart_json}", _json_for_script(payload))
        .replace("{draw_js}", draw_js or "window.masDraw = function() {};")
        .replace("/*__MAS_D3_INLINE__*/", d3_inline())
    )
