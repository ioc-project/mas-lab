#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""Shared ``custom.{owner}.plot`` envelope schema — the bridge between a
static viz/* pipeline step's rendered output and demo-ui's live plot host.

Background: ``library-lab/library.yaml`` already registers an ``artifact``
named ``plot`` (``mas.library.lab.artifact_types.Plot``), but that class only
describes a static file's on-disk path/format convention for the pipeline's
own caching — it has nothing to do with the live UI. Separately,
``demo-ui/src/plugins/html-plot.js`` is a generic iframe host that has been
ready to consume a ``custom.{owner}.plot`` envelope all along, but nothing in
this repo's pipeline steps (``ci_plot.py``, ``plot_trajectory.py``,
``plot_communication_flow.py``, ``metrics_comparison_plot.py``, ``plotnine.py``,
``pipeline_diagram.py`` — all write-a-static-file-and-stop) ever produced
one. This module is the missing, formal link: the
exact envelope shape ``html-plot.js``'s own ``chartOf()`` expects, as a single
function every viz/* step can call from the SAME render path that already
writes its static artefact — one render, two destinations — instead of each
step reinventing (or never building) its own glue to the live UI.

Envelope shape (verified against ``demo-ui/src/plugins/html-plot.js``):
``chartOf(payload)`` accepts a payload that is truthy on at least one of
``html | data | kind | agents | cards | nodes | roster | specialists |
stages | fragments``. For a plain static-image plot, ``html`` (a self-
contained HTML document, typically a base64-embedded ``<img>``) is the
field that actually gets painted: ``htmlPlotPlugin.mount()`` loads it once via
``iframe.srcdoc`` on the first tick where ``chart.html`` is a non-empty
string, then never reloads it in response to later ticks (``loadHtml()`` is
only re-invoked when a NEW ``html`` string is published) — i.e. this is
exactly "live: evolution; offline: latest snapshot" in a single-shot form
for a step, like ``ci_plot``, that renders once at the end of a run rather
than ticking incrementally like ``a streaming panel step``/``another streaming panel step``.
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from mas.library.lab.steps.services.file_tail import publish_custom_record

#: Fields html-plot.js's chartOf() checks for truthiness -- a plot envelope's
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
    HTML document (base64 data: URI, no external requests) -- suitable as a
    ``custom.{owner}.plot`` envelope's ``html`` field for html-plot.js's
    single-shot "latest snapshot" iframe.
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
    """Build a ``custom.{owner}.plot`` record matching html-plot.js's
    ``chartOf()`` contract exactly. Raises ``ValueError`` if the result would
    be silently dropped client-side (every ``PLOT_PAYLOAD_FIELDS`` entry
    empty) -- fail loudly here rather than publish a tick nothing will ever
    render, which would otherwise look identical to "ingest URL not
    configured" (also a silent no-op) and be just as hard to debug.
    """
    if not owner:
        raise ValueError("build_plot_record: owner is required")
    # NOTE: the envelope's own "kind" (the topic string, "custom.{owner}.plot")
    # is unconditionally non-empty by construction, and html-plot.js's
    # chartOf() treats bare payload.kind truthiness as sufficient on its own
    # -- so checking PLOT_PAYLOAD_FIELDS against the built record would never
    # actually catch anything (it would always pass via "kind" alone). The
    # real failure mode worth catching is a tick that carries no actual
    # content: neither "data" nor "html" set, which the client would accept
    # without complaint and then render nothing from.
    if not data and not html:
        raise ValueError(
            f"build_plot_record({owner!r}, kind={kind!r}): neither 'data' nor "
            "'html' was given -- this tick would publish successfully but "
            "html-plot.js has nothing to render from it"
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


def stream_plot_tick(step: Any, ctx: Any, *, kind: str, title: str, data: Dict[str, Any]) -> None:
    """Render the HTML shell once (``step._sent_html`` tracks that), then
    post a ``custom.{owner}.plot`` tick every call with the latest ``data``.

    ``step`` needs ``config`` (for ``owner``/``url``) and a ``_sent_html``
    attribute initialised in ``__init__``; ``ctx`` is only used to build
    the ``ref`` via ``file_tail.ref_from_ctx``.
    """
    from mas.library.lab.steps.services.file_tail import ref_from_ctx

    try:
        from mas.lab.plots.html_demo import render_html
    except ImportError:
        return
    owner = str(step.config.get("owner") or kind)
    html = render_html(kind, data, title=title) if not step._sent_html else None
    step._sent_html = step._sent_html or bool(html)
    post_plot_tick(
        owner,
        kind=kind,
        title=title,
        data=data,
        html=html,
        url=step.config.get("url"),
        ref=ref_from_ctx(step.config, ctx),
    )
