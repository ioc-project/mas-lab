#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""PipelineDiagramStep — render the current pipeline as a static SVG flow diagram.

Produces an SVG that mirrors the pipeline canvas in mas-lab-studio: nodes are
rounded rectangles with a colour-coded type badge, connected by dependency arrows
drawn left-to-right (or top-to-bottom) through a layered DAG layout.

No extra dependencies — pure-stdlib SVG generation.

Configuration
-------------
output     str     Output path (.svg or .png via cairosvg if installed).
                   Relative path under the level folder.
                   Default: ``results/pipeline.svg``
direction  str     ``"LR"`` (left-to-right) or ``"TB"`` (top-to-bottom).
                   Default: ``"LR"``
theme      str     ``"dark"`` (matches studio) or ``"light"``.
                   Default: ``"dark"``
steps      list    Optional explicit step list.  Each entry::
                     {name: "...", type: "...", depends_on: [...]}
                   When absent, the current pipeline's steps are used.
title      str     Optional diagram title rendered at the top.
width      int     Canvas width in px (auto-computed when absent).
height     int     Canvas height in px (auto-computed when absent).

Example YAML — auto-diagram of the running pipeline::

    experiment:
      post:
        - name: pipeline-diagram
          type: pipeline_diagram
          config:
            title: "Post-processing pipeline"

Example YAML — explicit step list (for documentation)::

    - name: diagram
      type: pipeline_diagram
      config:
        steps:
          - {name: collect-metrics, type: collect_metrics}
          - {name: compute-ci,      type: compute_ci,      depends_on: [collect-metrics]}
          - {name: ci-figure,       type: plotnine,        depends_on: [compute-ci]}
"""

import logging
import math
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path

logger = logging.getLogger(__name__)

# ── Colour tokens (matches pipeline-editor.css) ──────────────────────────────

_DARK = {
    "bg":            "#0d1117",
    "surface":       "#161b22",
    "overlay":       "#21262d",
    "border":        "#30363d",
    "border_accent": "#388bfd",
    "text_primary":  "#e6edf3",
    "text_secondary":"#8b949e",
    "text_code":     "#a5d6ff",
    "edge":          "#4d555e",
    "arrow":         "#58a6ff",
}
_LIGHT = {
    "bg":            "#ffffff",
    "surface":       "#f6f8fa",
    "overlay":       "#eaeef2",
    "border":        "#d0d7de",
    "border_accent": "#0969da",
    "text_primary":  "#1f2328",
    "text_secondary":"#656d76",
    "text_code":     "#0550ae",
    "edge":          "#8c959f",
    "arrow":         "#0969da",
}

# Step type → accent colour (same hue as the UI node type tokens)
_TYPE_COLOURS: Dict[str, str] = {
    "collect_metrics":       "#1f6feb",   # data (blue)
    "to_dataframe":          "#1f6feb",
    "join_dataframe":        "#1f6feb",
    "data_source":           "#1f6feb",
    "dataset":               "#3fb950",   # dataset (green)
    "normalize_events":      "#3fb950",
    "normalize_otel":        "#3fb950",
    "normalize_observe":     "#3fb950",
    "extract_trajectories":  "#3fb950",   # traj
    "embed_trajectories":    "#3fb950",
    "embed_states":          "#3fb950",
    "plotnine":              "#db6d28",   # plot (orange)
    "plot":                  "#db6d28",
    "ci_plot":               "#db6d28",
    "metrics_comparison_plot":"#db6d28",
    "plot_trajectory":       "#db6d28",
    "plot_trajectory_batch": "#db6d28",
    "eval_batch":            "#e3b341",   # eval (yellow)
    "eval_mce":              "#e3b341",
    "eval_adversarial":      "#e3b341",
    "eval_adversarial_kg":   "#e3b341",
    "compute_ci":            "#8957e5",   # manifest/compute (purple)
    "annotate_metrics":      "#8957e5",
    "neo4j_push":            "#40a4ce",   # info (cyan)
    "service_start":         "#40a4ce",
    "service_stop":          "#f85149",   # danger (red)
}
_DEFAULT_TYPE_COLOUR = "#6e7681"  # unknown (gray)

# Attachment / anchor node — amber, distinct from all data-processing types
_ATTACHMENT_COLOUR = "#d29922"

# Valid binding levels and phases for the anchor node
_BIND_LEVELS = {"run", "test", "scenario", "experiment", "*"}
_BIND_PHASES = {"pre", "post", "*"}


def _parse_bind(bind: str) -> tuple[str, str]:
    """Parse ``"level/phase"`` into ``(level, phase)``.

    Accepts:
    * ``"experiment/post"``  → (experiment, post)
    * ``"run"``              → (run, *)
    * ``"*"`` / ``""``       → (*, *)
    """
    if not bind or bind in ("*", "*/*"):
        return ("*", "*")
    if "/" in bind:
        level, phase = bind.split("/", 1)
    else:
        level, phase = bind, "*"
    level = level.strip() if level.strip() in _BIND_LEVELS else "*"
    phase = phase.strip() if phase.strip() in _BIND_PHASES else "*"
    return (level, phase)


def _anchor_label(bind: str) -> str:
    """Return the display label for an anchor node."""
    level, phase = _parse_bind(bind)
    if level == "*" and phase == "*":
        return "*/*"
    if phase == "*":
        return f"{level}/*"
    return f"{level}/{phase}"

# ── Layout constants ──────────────────────────────────────────────────────────

_NODE_W    = 180
_NODE_H    = 56
_H_GAP     = 60    # horizontal gap between layers
_V_GAP     = 20    # vertical gap between nodes in a layer
_PAD       = 40    # canvas padding
_BADGE_H   = 18
_BADGE_PAD = 6
_RADIUS    = 6     # node corner radius


# ── DAG layout ────────────────────────────────────────────────────────────────

def _assign_layers(
    nodes: List[str],
    deps: Dict[str, List[str]],
) -> Dict[str, int]:
    """Assign a layer index to each node (longest path = rightmost layer)."""
    layer: Dict[str, int] = {}

    def depth(n: str) -> int:
        if n in layer:
            return layer[n]
        preds = deps.get(n, [])
        if not preds:
            layer[n] = 0
        else:
            layer[n] = max(depth(p) for p in preds) + 1
        return layer[n]

    for n in nodes:
        depth(n)
    return layer


def _layout(
    steps: List[Dict[str, Any]],
    direction: str = "LR",
) -> Tuple[Dict[str, Tuple[float, float]], float, float]:
    """Return ``{name: (cx, cy)}`` centres, total (width, height)."""
    nodes  = [s["name"] for s in steps]
    deps   = {s["name"]: s.get("depends_on", []) for s in steps}
    layers = _assign_layers(nodes, deps)

    # Group nodes per layer
    from collections import defaultdict
    by_layer: Dict[int, List[str]] = defaultdict(list)
    for n, l in layers.items():
        by_layer[l].append(n)

    max_layer = max(by_layer.keys()) if by_layer else 0
    max_col   = max(len(v) for v in by_layer.values()) if by_layer else 1

    if direction == "LR":
        canvas_w = _PAD * 2 + (max_layer + 1) * _NODE_W + max_layer * _H_GAP
        canvas_h = _PAD * 2 + max_col * _NODE_H + (max_col - 1) * _V_GAP
    else:  # TB
        canvas_w = _PAD * 2 + max_col * _NODE_W + (max_col - 1) * _H_GAP
        canvas_h = _PAD * 2 + (max_layer + 1) * _NODE_H + max_layer * _H_GAP

    centres: Dict[str, Tuple[float, float]] = {}
    for lyr, ns in by_layer.items():
        col_count = len(ns)
        for idx, n in enumerate(sorted(ns)):
            if direction == "LR":
                cx = _PAD + _NODE_W / 2 + lyr * (_NODE_W + _H_GAP)
                total_h = col_count * _NODE_H + (col_count - 1) * _V_GAP
                cy = (canvas_h - total_h) / 2 + _NODE_H / 2 + idx * (_NODE_H + _V_GAP)
            else:
                cy = _PAD + _NODE_H / 2 + lyr * (_NODE_H + _H_GAP)
                total_w = col_count * _NODE_W + (col_count - 1) * _H_GAP
                cx = (canvas_w - total_w) / 2 + _NODE_W / 2 + idx * (_NODE_W + _H_GAP)
            centres[n] = (cx, cy)

    return centres, canvas_w, canvas_h


# ── SVG primitives ────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _cubic_path(x1: float, y1: float, x2: float, y2: float, direction: str) -> str:
    """Return an SVG cubic bezier path between two points."""
    if direction == "LR":
        dx = (x2 - x1) * 0.5
        return f"M {x1:.1f},{y1:.1f} C {x1+dx:.1f},{y1:.1f} {x2-dx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
    else:
        dy = (y2 - y1) * 0.5
        return f"M {x1:.1f},{y1:.1f} C {x1:.1f},{y1+dy:.1f} {x2:.1f},{y2-dy:.1f} {x2:.1f},{y2:.1f}"


def _render_svg(
    steps: List[Dict[str, Any]],
    direction: str = "LR",
    theme: str = "dark",
    title: str = "",
    override_w: Optional[int] = None,
    override_h: Optional[int] = None,
) -> str:
    tok = _DARK if theme == "dark" else _LIGHT

    title_h = 32 if title else 0
    centres, w, h = _layout(steps, direction)
    h += title_h

    if override_w:
        scale_x = override_w / w
        w = override_w
    else:
        scale_x = 1.0
    if override_h:
        scale_y = override_h / h
        h = override_h
    else:
        scale_y = 1.0

    def sx(v: float) -> float: return v * scale_x
    def sy(v: float) -> float: return v * scale_y
    def sxr(v: float) -> str: return f"{sx(v):.1f}"
    def syr(v: float) -> str: return f"{sy(v):.1f}"

    name_map = {s["name"]: s for s in steps}
    deps_map  = {s["name"]: s.get("depends_on", []) for s in steps}

    lines: List[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{sx(w):.0f}" height="{sy(h):.0f}" '
        f'viewBox="0 0 {sx(w):.1f} {sy(h):.1f}">'
    )

    # ── Defs: arrow marker ────────────────────────────────────────────────────
    lines.append(
        f'<defs>'
        f'<marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L0,6 L8,3 z" fill="{tok["arrow"]}"/>'
        f'</marker>'
        f'</defs>'
    )

    # ── Background ────────────────────────────────────────────────────────────
    lines.append(f'<rect width="{sx(w):.1f}" height="{sy(h):.1f}" fill="{tok["bg"]}" rx="8"/>')

    # ── Title ─────────────────────────────────────────────────────────────────
    if title:
        lines.append(
            f'<text x="{sx(w/2):.1f}" y="{sy(20):.1f}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-family="SFMono-Regular, Consolas, monospace" '
            f'font-size="{sy(13):.1f}" fill="{tok["text_secondary"]}">'
            f'{_esc(title)}</text>'
        )

    td = sy(title_h)  # vertical offset for title

    # ── Edges ─────────────────────────────────────────────────────────────────
    for name, preds in deps_map.items():
        if name not in centres:
            continue
        cx2, cy2 = centres[name]
        cy2 += title_h
        for pred in preds:
            if pred not in centres:
                continue
            cx1, cy1 = centres[pred]
            cy1 += title_h
            # Exit/enter at midpoints of node sides
            if direction == "LR":
                ex = cx1 + _NODE_W / 2
                ey = cy1
                ax = cx2 - _NODE_W / 2 - 8  # leave room for arrowhead
                ay = cy2
            else:
                ex = cx1
                ey = cy1 + _NODE_H / 2
                ax = cx2
                ay = cy2 - _NODE_H / 2 - 8
            path = _cubic_path(sx(ex), sy(ey), sx(ax), sy(ay), direction)
            lines.append(
                f'<path d="{path}" stroke="{tok["arrow"]}" stroke-width="1.5" '
                f'fill="none" opacity="0.7" marker-end="url(#arr)"/>'
            )

    # ── Nodes ─────────────────────────────────────────────────────────────────
    for step in steps:
        name = step["name"]
        stype = step.get("type", "unknown")
        if name not in centres:
            continue
        cx, cy = centres[name]
        cy += title_h

        x = sx(cx - _NODE_W / 2)
        y = sy(cy - _NODE_H / 2)
        nw = sx(_NODE_W)
        nh = sy(_NODE_H)
        accent = _TYPE_COLOURS.get(stype, _DEFAULT_TYPE_COLOUR)
        r = sy(_RADIUS)

        # ── Attachment anchor node (pill + dashed border) ─────────────────
        if stype == "_attachment":
            pill_r = nh / 2
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{nw:.1f}" height="{nh:.1f}" '
                f'rx="{pill_r:.1f}" fill="{tok["bg"]}" '
                f'stroke="{_ATTACHMENT_COLOUR}" stroke-width="1.5" '
                f'stroke-dasharray="5 3"/>'
            )
            # Centred label: ⊙ level/phase
            label = "⊙ " + (name if len(name) <= 18 else name[:16] + "…")
            lines.append(
                f'<text x="{sx(cx):.1f}" y="{sy(cy):.1f}" '
                f'text-anchor="middle" dominant-baseline="middle" '
                f'font-family="SFMono-Regular, Consolas, monospace" '
                f'font-size="{sy(11):.1f}" font-weight="500" fill="{_ATTACHMENT_COLOUR}">'
                f'{_esc(label)}</text>'
            )
            continue

        # Node box
        lines.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{nw:.1f}" height="{nh:.1f}" '
            f'rx="{r:.1f}" fill="{tok["surface"]}" '
            f'stroke="{tok["border"]}" stroke-width="1"/>'
        )
        # Accent left stripe
        stripe_w = sx(4)
        lines.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{stripe_w:.1f}" height="{nh:.1f}" '
            f'rx="{r:.1f}" fill="{accent}"/>'
        )
        # Fix stripe right corners
        lines.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{stripe_w:.1f}" height="{nh:.1f}" '
            f'rx="0" fill="{accent}" clip-path="none" '
            f'transform="translate({stripe_w:.1f},0) scale(-1,1) translate({-stripe_w:.1f},0)"/>'
        )
        # Simpler approach: just draw the stripe without right-side rounding
        # Re-draw as simple rect anchored at left
        lines.pop()  # remove the broken clip attempt
        lines.pop()  # remove the first stripe attempt
        lines.append(
            f'<clipPath id="clip-{_esc(name)}">'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{nw:.1f}" height="{nh:.1f}" rx="{r:.1f}"/>'
            f'</clipPath>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{stripe_w:.1f}" height="{nh:.1f}" '
            f'fill="{accent}" clip-path="url(#clip-{_esc(name)})"/>'
        )

        # Name label
        label = name
        if len(label) > 22:
            label = label[:20] + "…"
        label_x = sx(cx - _NODE_W / 2 + 12)
        label_y = sy(cy - 10)
        lines.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" '
            f'dominant-baseline="middle" '
            f'font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" '
            f'font-size="{sy(12):.1f}" font-weight="500" fill="{tok["text_primary"]}">'
            f'{_esc(label)}</text>'
        )

        # Type badge
        badge_label = stype if len(stype) <= 18 else stype[:16] + "…"
        badge_w = sx(min(len(badge_label) * 6.5 + _BADGE_PAD * 2, _NODE_W - 20))
        badge_x = sx(cx - _NODE_W / 2 + 12)
        badge_y = sy(cy + 6)
        lines.append(
            f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" '
            f'width="{badge_w:.1f}" height="{sy(_BADGE_H):.1f}" '
            f'rx="{sy(3):.1f}" fill="{accent}" opacity="0.18"/>'
        )
        lines.append(
            f'<text x="{badge_x + badge_w/2:.1f}" y="{badge_y + sy(_BADGE_H)/2:.1f}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-family="SFMono-Regular, Consolas, monospace" '
            f'font-size="{sy(10):.1f}" fill="{accent}">'
            f'{_esc(badge_label)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ── Step implementation ───────────────────────────────────────────────────────

class PipelineDiagramStep(PipelineStep):
    """Render the current (or an explicit) pipeline as a static SVG flow diagram.

    Uses the same colour tokens and node style as mas-lab-studio's pipeline canvas.
    No external dependencies — pure Python SVG generation.
    """

    type = "pipeline_diagram"

    async def execute(self, ctx) -> StepOutput:
        cfg = self.config

        # ── Resolve step list ─────────────────────────────────────────────────
        raw_steps: Optional[List[Dict[str, Any]]] = cfg.get("steps")
        if raw_steps is None:
            # Use the live pipeline from context
            pipeline = getattr(ctx, "pipeline", None)
            if pipeline is None:
                raise RuntimeError(
                    "PipelineDiagramStep: no 'steps' in config and no pipeline "
                    "found in execution context."
                )
            raw_steps = [
                {
                    "name": s.name,
                    "type": getattr(s, "step_type", s.__class__.__name__),
                    "depends_on": list(s.depends_on),
                }
                for s in pipeline.steps
            ]

        # Normalise: ensure each entry has name, type, depends_on
        steps: List[Dict[str, Any]] = []
        for entry in raw_steps:
            if isinstance(entry, str):
                steps.append({"name": entry, "type": "unknown", "depends_on": []})
            else:
                steps.append({
                    "name": entry.get("name", "?"),
                    "type": entry.get("type", "unknown"),
                    "depends_on": list(entry.get("depends_on", [])),
                    "scope": entry.get("scope", ""),
                    "phase": entry.get("phase", "post"),
                })

        # ── Anchor / attachment node ──────────────────────────────────────────
        # Resolve the binding: config key > pipeline.bind attribute > step scopes
        attach_cfg = cfg.get("attach")  # explicit in diagram config; False = disable
        if attach_cfg is not False:
            if attach_cfg and isinstance(attach_cfg, str):
                bind_str = attach_cfg
            else:
                # Try pipeline object
                pipeline_obj = getattr(ctx, "pipeline", None)
                bind_str = getattr(pipeline_obj, "bind", None) if pipeline_obj else None
                if not bind_str:
                    # Infer from steps' scope/phase
                    scopes = {s["scope"] for s in steps if s.get("scope")}
                    phases = {s["phase"] for s in steps if s.get("phase")}
                    level = next(iter(scopes), "*")
                    phase = next(iter(phases), "*") if len(phases) == 1 else "*"
                    bind_str = f"{level}/{phase}"

            anchor_label = _anchor_label(bind_str or "*/*")
            anchor = {
                "name": anchor_label,
                "type": "_attachment",
                "depends_on": [],
                "scope": "",
                "phase": "",
            }
            # Wire all root nodes (no existing deps) to the anchor
            steps = [
                {**s, "depends_on": [anchor_label]} if not s.get("depends_on") else s
                for s in steps
            ]
            steps = [anchor] + steps

        # ── Output path ───────────────────────────────────────────────────────
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "pipeline.svg", cfg, default_subdir="results", mkdir=True
        )

        # ── Render ────────────────────────────────────────────────────────────
        direction = cfg.get("direction", "LR")
        theme     = cfg.get("theme", "dark")
        title     = cfg.get("title", "")
        override_w = cfg.get("width")
        override_h = cfg.get("height")

        svg = _render_svg(
            steps,
            direction=direction,
            theme=theme,
            title=title,
            override_w=override_w,
            override_h=override_h,
        )

        # ── Write (SVG or PNG via cairosvg) ──────────────────────────────────
        suffix = output_path.suffix.lower()
        if suffix == ".png":
            try:
                import cairosvg  # type: ignore[import]
                cairosvg.svg2png(bytestring=svg.encode(), write_to=str(output_path))
            except ImportError:
                svg_path = output_path.with_suffix(".svg")
                svg_path.write_text(svg, encoding="utf-8")
                logger.warning(
                    "PipelineDiagramStep: cairosvg not installed — saved SVG to %s "
                    "instead of PNG.", svg_path
                )
                output_path = svg_path
        else:
            output_path.write_text(svg, encoding="utf-8")

        logger.info("PipelineDiagramStep '%s': saved %s (%d steps)", self.name, output_path, len(steps))
        return StepOutput(
            data={"output": str(output_path)},
            files=[output_path],
            metadata={"output": str(output_path), "steps": len(steps)},
        )
