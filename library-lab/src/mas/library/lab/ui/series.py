#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic timeseries plot (``loop`` / ``series``).

Registered as ``mas.lab.ui.plots`` so the demo-ui host never hardcodes this
module. Layouts pass ``plugin: plot`` with ``kind: loop`` and a ``stream``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mas.lab.plots.live_html import FILE_FORMATS, LIVE_FORMATS, chart_payload, render_live

_DRAW = Path(__file__).parent / "assets" / "series.js"

SUPPORTED_KINDS = frozenset({"loop", "series"})
ARTEFACT_STREAM = {
    "loop": "series",
    "series": "series",
}


def stream_for(kind: str) -> str:
    name = str(kind or "").strip()
    if name not in SUPPORTED_KINDS:
        raise ValueError(f"unknown series plot kind {name!r}; use one of {sorted(SUPPORTED_KINDS)}")
    return ARTEFACT_STREAM[name]


def _as_y(value: Any) -> float | None:
    raw = value.get("y", value.get("value")) if isinstance(value, dict) else value
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _series_named(data: dict[str, Any]) -> dict[str, list[float]]:
    named: dict[str, list[float]] = {}
    series = data.get("series")
    if isinstance(series, dict):
        for label, ys in series.items():
            if isinstance(ys, list):
                named[str(label)] = [y for y in (_as_y(item) for item in ys) if y is not None]
    points = data.get("points") or data.get("values")
    if isinstance(points, list) and points:
        key = str(data.get("instance") or data.get("kind") or "series")
        named.setdefault(key, [y for y in (_as_y(item) for item in points) if y is not None])
    return named


def render_svg(kind: str, data: dict[str, Any], *, title: str = "") -> str:
    payload = chart_payload(kind, data, title=title)
    named = _series_named(payload["data"])
    title_esc = (
        str(payload["title"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    if not named:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="80">'
            f'<text x="12" y="48" font-size="14" font-family="sans-serif">{title_esc}</text></svg>'
        )
    width, height, pad = 720, 220, 36
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    palette = ("#38bdf8", "#fb923c", "#86efac", "#f87171", "#c4b5fd")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<text x="{pad}" y="20" font-size="13" fill="#94a3b8">{title_esc}</text>',
    ]
    for index, (label, ys) in enumerate(named.items()):
        if len(ys) < 2:
            continue
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or 1.0
        n = max(1, len(ys) - 1)
        pts = []
        for i, y in enumerate(ys):
            x = pad + inner_w * (i / n)
            py = pad + inner_h * (1.0 - (y - lo) / span)
            pts.append(f"{x:.1f},{py:.1f}")
        color = palette[index % len(palette)]
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{" ".join(pts)}"/>'
        )
        parts.append(
            f'<text x="{width - pad}" y="{24 + 14 * (index + 1)}" text-anchor="end" '
            f'font-size="11" fill="{color}">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_png(kind: str, data: dict[str, Any], *, title: str = "") -> bytes:
    raise ValueError("png needs a matplotlib extra; use html or svg for live timeseries")


def render_html(kind: str, data: dict[str, Any], *, title: str = "", fmt: str = "html") -> str | bytes:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unknown series plot kind {kind!r}; use one of {sorted(SUPPORTED_KINDS)}")
    fmt = str(fmt or "html").lower()
    if fmt == "json":
        return json.dumps(chart_payload(kind, data, title=title), ensure_ascii=False, default=str)
    if fmt in FILE_FORMATS:
        if fmt == "svg":
            return render_svg(kind, data, title=title)
        return render_png(kind, data, title=title)
    if fmt not in LIVE_FORMATS:
        raise ValueError(f"plot format must be html, svg, or png (got {fmt!r})")
    return render_live(kind, data, draw_js=_DRAW.read_text(encoding="utf-8"), title=title, fmt=fmt)


class SeriesPlots:
    """PlotProvider for generic detector / metric timeseries."""

    kinds = SUPPORTED_KINDS

    def stream_for(self, kind: str) -> str:
        return stream_for(kind)

    def render_html(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        title: str = "",
        fmt: str = "html",
    ) -> str | bytes:
        return render_html(kind, data, title=title, fmt=fmt)

    def render_embedded(self, kind: str) -> dict[str, str]:
        return {"kind": kind, "css": "", "js": _DRAW.read_text(encoding="utf-8")}
