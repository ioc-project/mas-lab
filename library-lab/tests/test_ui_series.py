#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from mas.library.lab.ui.series import SeriesPlots, render_html, stream_for


def test_stream_for_is_generic_series() -> None:
    assert stream_for("loop") == "series"
    assert stream_for("series") == "series"
    with pytest.raises(ValueError, match="unknown series plot kind"):
        stream_for("chat")


def test_render_loop_html_has_ticks_and_labels() -> None:
    html = render_html(
        "loop",
        {"series": {"agents": [0.1, 0.4, 0.8]}, "labels": {"agents": "Agents"}},
        title="Loop",
        fmt="html",
    )
    assert "drawSeries" in html
    assert "masUpdateChart" in html
    assert "xtick" in html
    assert "prettyLabel" in html
    assert "parseTime" in html
    assert "recordingT0" in html
    assert "t - t0" in html
    assert "toFixed(1)" in html
    assert "!Number.isFinite(t)" in html
    assert "prev.y" in html
    assert "seriesColor" in html
    assert "series-dot" in html
    assert "series-hold" in html
    assert "series-hold-dot" in html
    assert "holdPts" in html
    assert "playhead" in html
    assert "HOLD_STEP" in html
    assert "0.1" in html
    assert "diracColor" in html
    assert "seedOriginSeries" in html
    assert "#ef4444" in html
    assert "#22c55e" in html
    assert "nowHint" in html
    assert "data.now" in html
    assert "data.t1" in html
    assert "series-stack" in html
    assert "drawOne" in html
    assert "!Object.keys(data[key]).length" in html
    assert "Agents" in html
    assert "drawChat" not in html
    assert "drawStory" not in html


def test_svg_polyline() -> None:
    svg = render_html("series", {"series": {"a": [0.1, 0.4, 0.9]}}, fmt="svg")
    assert "<svg" in str(svg)
    assert "polyline" in str(svg)


def test_provider_surface() -> None:
    plots = SeriesPlots()
    assert "loop" in plots.kinds
    blob = plots.render_html("loop", {"series": {}}, fmt="json")
    assert json.loads(blob)["kind"] == "loop"
