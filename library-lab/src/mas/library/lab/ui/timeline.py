#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Environment timeline lane (fixture bars, trigger diamonds, action spans)."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mas.lab.plots.live_html import FILE_FORMATS, LIVE_FORMATS, chart_payload, render_live

_DRAW = Path(__file__).parent / "assets" / "timeline.js"

SUPPORTED_KINDS = frozenset({"timeline"})
ARTEFACT_STREAM = {"timeline": "custom.environment"}


def stream_for(kind: str) -> str:
    name = str(kind or "").strip()
    if name not in SUPPORTED_KINDS:
        raise ValueError(f"unknown timeline plot kind {name!r}")
    return ARTEFACT_STREAM[name]


def _events(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("events") or data.get("records") or data.get("points") or []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _wall_ms(event: dict[str, Any], index: int) -> float:
    time_map = event.get("time") if isinstance(event.get("time"), dict) else {}
    wall = str(time_map.get("wall") or event.get("wall") or "")
    if wall:
        try:
            return datetime.fromisoformat(wall.replace("Z", "+00:00")).timestamp() * 1000.0
        except ValueError:
            pass
    elapsed = time_map.get("elapsed_ms")
    if isinstance(elapsed, (int, float)):
        return float(elapsed)
    return float(index)


def _lane(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "")
    name = str(event.get("name") or "")
    if kind == "fixture" or name.startswith("fixture."):
        source = event.get("source") if isinstance(event.get("source"), dict) else {}
        return f"fixture:{source.get('id') or source.get('fixture') or 'fixture'}"
    if kind == "trigger" or name.startswith("trigger."):
        return "triggers"
    if kind == "action" or name.startswith("action."):
        return "actions"
    if kind == "clock" or name.startswith("clock."):
        return "clock"
    if kind == "timer" or name.startswith("timer."):
        return "triggers"
    return "events"


def _esc(value: Any) -> str:
    """Records may come from a cached file or a foreign stream: escape every text node."""
    return html.escape(str(value), quote=True)


def render_svg(kind: str, data: dict[str, Any], *, title: str = "") -> str:
    payload = chart_payload(kind, data, title=title)
    events = _events(payload["data"] if isinstance(payload.get("data"), dict) else data)
    title_esc = _esc(payload.get("title") or title)
    stamped = [(_wall_ms(event, index), event) for index, event in enumerate(events)]
    if not stamped:
        stamped = [(0.0, {"kind": "clock", "name": "clock.started"})]
    t0 = min(item[0] for item in stamped)
    t1 = max(item[0] for item in stamped)
    span = max(1.0, t1 - t0)
    lanes = list(dict.fromkeys(_lane(event) for _, event in stamped))
    if not lanes:
        lanes = ["events"]
    width, pad, lane_h = 720, 24, 28
    height = 40 + lane_h * len(lanes) + pad
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<text x="{pad}" y="18" font-size="13" fill="#334155">{title_esc or "environment"}</text>',
    ]
    colors = {
        "fixture": "#38bdf8",
        "trigger": "#f59e0b",
        "action": "#22c55e",
        "timer": "#a78bfa",
        "clock": "#64748b",
        "event": "#94a3b8",
    }
    axis_y0 = 32
    for lane_index, lane in enumerate(lanes):
        y = axis_y0 + lane_index * lane_h
        parts.append(
            f'<text x="8" y="{y + 14}" font-size="10" fill="#64748b">{_esc(lane.split(":", 1)[-1][:16])}</text>'
        )
        parts.append(
            f'<line x1="{pad + 80}" y1="{y + 12}" x2="{width - pad}" y2="{y + 12}" stroke="#cbd5e1" stroke-width="1"/>'
        )
        lane_events = [(t, ev) for t, ev in stamped if _lane(ev) == lane]
        starts: dict[str, float] = {}
        for t, event in lane_events:
            x = pad + 80 + (width - pad * 2 - 80) * ((t - t0) / span)
            name = str(event.get("name") or "")
            kind_name = str(event.get("kind") or "event")
            color = colors.get(kind_name, "#64748b")
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            key = str(source.get("id") or name)
            if name.endswith(".ready") or name.endswith(".started") or name.endswith(".armed"):
                starts[key] = x
            ended = name.endswith((".stopped", ".done", ".failed", ".timeout"))
            if ended:
                x0 = starts.pop(key, x - 16)
                outcome = str(event.get("outcome") or "ok")
                payload_map = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                failed = outcome in {"failed", "timeout"} or payload_map.get("forced")
                fill = "#ef4444" if failed else color
                w = max(8.0, abs(x - x0))
                parts.append(
                    f'<rect x="{min(x0, x):.1f}" y="{y + 4}" width="{w:.1f}" height="16" fill="{fill}" opacity="0.7"/>'
                )
            elif kind_name == "trigger" or name.startswith("trigger."):
                occ = ""
                payload_map = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if payload_map.get("occurrence") is not None:
                    occ = f" #{payload_map.get('occurrence')}"
                label = f"{source.get('id') or ''}{occ}".strip()
                parts.append(
                    f'<polygon points="{x:.1f},{y} {x + 7:.1f},{y + 12} '
                    f'{x:.1f},{y + 24} {x - 7:.1f},{y + 12}" '
                    + (f'fill="none" stroke="{color}"/>' if name == "trigger.skipped" else f'fill="{color}"/>')
                )
                if label:
                    parts.append(
                        f'<text x="{x + 8:.1f}" y="{y + 10}" font-size="9" fill="#334155">{_esc(label[:24])}</text>'
                    )
            else:
                parts.append(f'<rect x="{x - 4:.1f}" y="{y + 6}" width="8" height="12" rx="2" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(kind: str, data: dict[str, Any], *, title: str = "", fmt: str = "html") -> str | bytes:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unknown timeline plot kind {kind!r}")
    fmt = str(fmt or "html").lower()
    if fmt == "json":
        return json.dumps(chart_payload(kind, data, title=title), ensure_ascii=False, default=str)
    if fmt in FILE_FORMATS:
        return render_svg(kind, data, title=title)
    if fmt not in LIVE_FORMATS:
        raise ValueError(f"plot format must be html or svg (got {fmt!r})")
    return render_live(kind, data, draw_js=_DRAW.read_text(encoding="utf-8"), title=title, fmt=fmt)


class TimelinePlots:
    kinds = SUPPORTED_KINDS

    def stream_for(self, kind: str) -> str:
        return stream_for(kind)

    def render_html(self, kind: str, data: dict[str, Any], *, title: str = "", fmt: str = "html") -> str | bytes:
        return render_html(kind, data, title=title, fmt=fmt)

    def render_embedded(self, kind: str) -> dict[str, str]:
        return {"kind": kind, "css": "", "js": _DRAW.read_text(encoding="utf-8")}
