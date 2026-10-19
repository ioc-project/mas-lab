#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from mas.lab.plots.html_demo import HtmlDemoPlotter, plot_demo, render_html, stream_for


def test_render_chat_html_embeds_update_hook() -> None:
    html = render_html("chat", {"cards": [{"role": "user", "text": "hi"}]}, title="Chat", fmt="html")
    assert "masUpdateChart" in html
    assert 'k.indexOf(".") >= 0' in html
    assert "masLockKind" in html
    assert "Chat" in html
    assert "d3" in html.lower()
    assert "drawChat" in html
    assert "drawSeries" not in html
    assert "drawStory" not in html
    assert "el.select(\".idle\").remove()" in html
    assert 'cls += " user"' in html
    assert "pinUser === true" in html
    assert "TEXT_LIMIT" in html
    assert 'key === "activity"' in html
    assert "data.now" in html
    assert "tool-fold" in html
    assert "c.tools" in html
    assert '+ "…</summary><div class="text">\'' not in html
    assert "cardDepth" in html
    assert "margin-left" in html
    assert "#facc15" in html
    assert "slice(0, 8)" not in html
    assert "n > 1e9" not in html
    assert "toFixed(1)" in html
    assert "data.t0" in html
    assert "lastTick" in html
    assert "lastMatching" in html
    assert "tickRunning" in html
    assert "paletteIndex" in html
    assert "TOOL_CAP" in html
    assert "WAVE_GAP" in html
    assert "explodeTurns" in html
    assert "nestCards" not in html
    assert "cardDepth(c) * 20" in html
    assert "scrollHeight" in html
    assert "function esc" in html
    assert ":scope > div.bubble" in html
    assert "tools-more" in html
    assert "border-left-color" in html
    assert 'class="who"' in html
    assert "color-mix" in html
    assert "font-size: 12px" in html


def test_render_topology_html() -> None:
    html = render_html(
        "topology",
        {"nodes": [{"id": "coordinator", "role": "entry"}], "edges": []},
        title="Topology",
        fmt="html",
    )
    assert "masUpdateChart" in html
    assert "drawTopology" in html
    assert "drawSeries" not in html
    assert "agentColor" in html
    assert "nodeRunning" in html
    assert "mas-pulse" in html
    assert "markerIds" in html
    assert "rememberPackets" in html
    assert "packetIsReturn" in html
    assert "ellipse.packet" in html
    assert "edgeEnds" in html
    assert "markHot" in html
    assert "kickPacketLoop" in html
    assert "data.idle" in html
    assert "__masNeedIdlePaint" in html
    assert "slice(0, 8)" not in html
    assert 'attr("fill", "#1e293b")' not in html


def test_json_and_svg_formats() -> None:
    blob = plot_demo("chat", {"cards": []}, fmt="json")
    payload = json.loads(blob)
    assert payload["kind"] == "chat"
    svg = plot_demo("topology", {"nodes": []}, fmt="svg")
    assert "<svg" in str(svg)
    with pytest.raises(ValueError, match="html, svg, or png"):
        plot_demo("topology", {"nodes": []}, fmt="pdf")


def test_stream_for_is_the_artefact_owner() -> None:
    assert stream_for("chat") == "custom.chat"
    assert stream_for("topology") == "custom.topology"
    with pytest.raises(ValueError, match="unknown demo plot kind"):
        stream_for("loop")
    with pytest.raises(ValueError, match="unknown demo plot kind"):
        stream_for("story")
    with pytest.raises(ValueError, match="unknown demo plot kind"):
        stream_for("heatmap")


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unknown demo plot kind"):
        plot_demo("heatmap", {"agents": []})


def test_html_demo_plotter_writes_file(tmp_path) -> None:
    dest = tmp_path / "chat.html"
    out = HtmlDemoPlotter().process(
        dest,
        kind="chat",
        data={"cards": []},
        output=dest,
    )
    assert dest.is_file()
    assert out.format == "html"
    assert "masUpdateChart" in dest.read_text(encoding="utf-8")
