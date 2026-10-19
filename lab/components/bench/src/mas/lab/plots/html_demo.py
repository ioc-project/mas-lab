#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mas-lab live plots: chat and topology.

Timeseries lives in ``mas.library.lab.ui.series``. Other kinds register
via ``mas.lab.ui.plots``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mas.lab.artifacts import PlotFile
from mas.lab.plots.live_html import FILE_FORMATS, LIVE_FORMATS, chart_payload, render_live
from mas.lab.processor import Processor, register

_DRAW = Path(__file__).parent / "assets" / "mas.js"

SUPPORTED_KINDS = frozenset({"chat", "topology"})
ARTEFACT_STREAM = {
    "chat": "custom.chat",
    "topology": "custom.topology",
}

_EXTRA_CSS = """
    .bubble { margin: 8px 0; padding: 7px 10px; border-left: 4px solid #38bdf8; background: #111827; border-radius: 0 6px 6px 0; }
    .bubble.nested { margin: 6px 0 6px 0; padding: 6px 8px; }
    .bubble.depth-1 { margin-left: 0.7rem; }
    .bubble.depth-2 { margin-left: 1.4rem; }
    .bubble.depth-3 { margin-left: 2.1rem; }
    .bubble.user { border-left-color: #facc15; }
    .bubble.user.pin { position: sticky; top: 0; z-index: 1; }
    .bubble.action.ok { border-left-color: #86efac; }
    .bubble.action.bad { border-left-color: #fecaca; }
    .bubble .text { white-space: pre-wrap; font-size: 13.5px; line-height: 1.45; color: #f1f5f9; }
    .bubble details { margin: 0; }
    .bubble details > summary { cursor: pointer; }
    .bubble .tools { margin-top: 6px; border: 0; background: transparent; font-size: 12px; opacity: 1; }
    .bubble .tool-fold { background: #0f172a; padding: 2px 6px; border: 1px solid #334155; border-radius: 4px; margin: 3px 0; }
    .bubble .tool-fold > summary { color: #cbd5e1; font-size: 12px; line-height: 1.35; font-weight: 500; }
    .bubble .tool-io { margin: 4px 0 2px; white-space: pre-wrap; color: #94a3b8; font: 11px/1.35 ui-monospace, monospace; }
    .bubble .tools-more > summary { color: #94a3b8; font-size: 12px; padding: 2px 4px; }
    .meta { color: #64748b; font-size: 11px; margin-bottom: 2px; }
    .meta .who { font-weight: 700; }
    .node circle { fill-opacity: 0.28; }
    .node.running circle {
      transform-box: fill-box;
      transform-origin: center;
      animation: mas-pulse 0.9s ease-in-out infinite;
    }
    .edge.running { stroke-dasharray: 7 5; animation: mas-dash 0.55s linear infinite; }
    .packet {
      stroke: #0f172a; stroke-width: 0.6;
      filter: drop-shadow(0 0 8px rgba(248, 113, 113, 0.85)) drop-shadow(0 0 3px rgba(255, 80, 80, 0.55));
    }
    .packet.back {
      filter: drop-shadow(0 0 8px rgba(125, 211, 252, 0.9)) drop-shadow(0 0 3px rgba(14, 165, 233, 0.55));
    }
    @keyframes mas-pulse {
      0%, 100% { stroke-opacity: 1; stroke-width: 2.5; transform: scale(1); }
      50% { stroke-opacity: 0.5; stroke-width: 5; transform: scale(1.22); }
    }
    @keyframes mas-dash { to { stroke-dashoffset: -20; } }
"""


def stream_for(kind: str) -> str:
    """Subscribe path for this plot's input artefact."""
    name = str(kind or "").strip()
    if name not in SUPPORTED_KINDS:
        raise ValueError(f"unknown demo plot kind {name!r}; use one of {sorted(SUPPORTED_KINDS)}")
    return ARTEFACT_STREAM[name]


def render_svg(kind: str, data: dict[str, Any], *, title: str = "") -> str:
    payload = chart_payload(kind, data, title=title)
    title_esc = (
        str(payload["title"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="80">'
        f'<text x="12" y="48" font-size="14" font-family="sans-serif">{title_esc}</text></svg>'
    )


def render_png(kind: str, data: dict[str, Any], *, title: str = "") -> bytes:
    raise ValueError("png is not supported for chat/topology; use html or svg")


def render_html(kind: str, data: dict[str, Any], *, title: str = "", fmt: str = "html") -> str | bytes:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unknown demo plot kind {kind!r}; use one of {sorted(SUPPORTED_KINDS)}")
    fmt = str(fmt or "html").lower()
    if fmt == "json":
        return json.dumps(chart_payload(kind, data, title=title), ensure_ascii=False, default=str)
    if fmt in FILE_FORMATS:
        if fmt == "svg":
            return render_svg(kind, data, title=title)
        return render_png(kind, data, title=title)
    if fmt not in LIVE_FORMATS:
        raise ValueError(f"plot format must be html, svg, or png (got {fmt!r})")
    return render_live(
        kind,
        data,
        draw_js=_DRAW.read_text(encoding="utf-8"),
        extra_css=_EXTRA_CSS,
        title=title,
        fmt=fmt,
    )


def plot_demo(
    kind: str,
    data: dict[str, Any],
    *,
    fmt: str = "html",
    title: str = "",
    output: str | Path | None = None,
) -> str | bytes:
    """Render a mas-lab chat or topology plot."""
    text = render_html(kind, data, title=title, fmt=fmt)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return text


class HtmlDemoPlots:
    """PlotProvider for chat + topology (discovered first by ``load_plot_providers``)."""

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
        return {
            "kind": kind,
            "css": _EXTRA_CSS,
            "js": _DRAW.read_text(encoding="utf-8"),
        }


@register
class HtmlDemoPlotter(Processor):
    """Render a chat / topology plot from its input artefact."""

    name = "html-demo-plotter"
    input_kind = "file"
    output_kind = "plot_file"
    description = "Mas-lab chat/topology plots (html live; svg end-state)"
    priority = 2

    def process(self, artifact: Any, **kwargs: Any) -> PlotFile:  # type: ignore[override]
        kind = str(kwargs.get("kind") or "chat").strip()
        fmt = str(kwargs.get("fmt") or kwargs.get("format") or "html").lower()
        title = str(kwargs.get("title") or kind.replace("_", " ").title())
        data = kwargs.get("data")
        if data is None:
            data = getattr(artifact, "data", None)
        if not isinstance(data, dict):
            path = getattr(artifact, "path", None) or artifact
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("html-demo-plotter expects a dict payload or a JSON file")
        out_path = kwargs.get("output")
        if out_path:
            out_path = Path(out_path)
        text = plot_demo(kind, data, fmt=fmt, title=title, output=out_path)
        return PlotFile(data=text, format=fmt, path=out_path)
