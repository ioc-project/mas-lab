#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""One-shot publish of a finished viz file (e.g. ``ci_plot``) onto EventStream.

Streaming folds do not use this. They publish the input artefact
(``custom.{owner}.snapshot`` / ``.panel``). The plot processor reads that
artefact; the live host subscribes via ``stream_for(kind)``.
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from mas.library.lab.steps.services.file_tail import publish_custom_record

#: Fields the embedded plot host treats as chart content. A plot envelope's
#: payload must set at least one of these or the client silently drops it.
PLOT_PAYLOAD_FIELDS = (
    "html",
    "data",
    "kind",
    "agents",
    "cards",
    "nodes",
    "roster",
    "specialists",
    "stages",
    "fragments",
)


def image_html_shell(image_path: Path, *, title: str = "") -> str:
    """Wrap a rendered image file (PNG/SVG/etc.) as a minimal, self-contained
    HTML document (base64 data: URI, no external requests) for pipeline
    end-state export. The live host does not load this HTML; it mounts
    ``render_embedded`` and feeds JSON ticks into ``masDraw``.
    """
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    safe_title = str(title or image_path.name).replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title>"
        "<style>html,body{margin:0;height:100%;background:#0f172a;"
        "display:flex;align-items:center;justify-content:center;}"
        "img{max-width:100%;max-height:100%;object-fit:contain;}</style>"
        f"</head><body><img src=\"data:{mime};base64,{encoded}\" alt=\"{safe_title}\"/></body></html>"
    )


def build_plot_record(
    owner: str,
    *,
    kind: str,
    title: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    html: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a ``custom.{owner}.plot`` record the embedded host can fold.
    Raises ``ValueError`` if the result would be silently dropped client-side
    (every ``PLOT_PAYLOAD_FIELDS`` entry empty).
    """
    if not owner:
        raise ValueError("build_plot_record: owner is required")
    # The envelope kind ("custom.{owner}.plot") is always set. Catch ticks
    # that carry no chart content (neither data nor html).
    if not data and not html:
        raise ValueError(
            f"build_plot_record({owner!r}, kind={kind!r}): neither 'data' nor "
            "'html' was given -- this tick would publish successfully but "
            "the plot host has nothing to render from it"
        )
    record: Dict[str, Any] = {"kind": f"custom.{owner}.plot", "plot_kind": kind}
    if title:
        record["title"] = title
    if data:
        record["data"] = data
    if html:
        record["html"] = html
    return record


def post_plot_tick(
    owner: str,
    *,
    kind: str,
    title: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    html: Optional[str] = None,
    url: Optional[str] = None,
    ref: Optional[Dict[str, Any]] = None,
) -> None:
    """Build and publish one ``custom.{owner}.plot`` tick onto EventStream."""
    del url
    record = build_plot_record(owner, kind=kind, title=title, data=data, html=html)
    publish_custom_record(record, ref=ref)


def stream_plot_tick(
    step: Any,
    ctx: Any,
    *,
    kind: str,
    title: str,
    data: Dict[str, Any],
    render: Any = None,
) -> None:
    """Publish JSON chart data on ``custom.{owner}.plot``.

    The live host loads ``GET /api/plots/{kind}?mode=embedded`` once and
    feeds these ticks into ``masDraw``. Do not attach HTML here — a
    document tick fights the data tick. ``render`` is unused (kept so
    existing callers do not change).
    """
    from mas.library.lab.steps.services.file_tail import ref_from_ctx

    del render
    owner = str(step.config.get("owner") or kind)
    post_plot_tick(
        owner,
        kind=kind,
        title=title,
        data=data,
        html=None,
        url=step.config.get("url"),
        ref=ref_from_ctx(step.config, ctx),
    )
