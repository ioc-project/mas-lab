#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import pytest
from mas.library.lab.ui.timeline import TimelinePlots, render_html, render_svg, stream_for


def test_stream_for_timeline() -> None:
    assert stream_for("timeline") == "custom.environment"
    assert TimelinePlots().stream_for("timeline") == "custom.environment"


def test_render_svg_ignores_non_list_events() -> None:
    svg = render_svg("timeline", {"events": {"kind": "fixture"}}, title="empty")
    assert "svg" in svg


def test_render_svg_small_event_list() -> None:
    svg = render_svg(
        "timeline",
        {
            "events": [
                {"kind": "fixture", "name": "fixture.ready"},
                {"kind": "trigger", "name": "trigger.matched"},
                {"kind": "action", "name": "action.done"},
            ]
        },
        title="revocation",
    )
    assert "svg" in svg
    assert "revocation" in svg
    timed = render_svg(
        "timeline",
        {
            "events": [
                {
                    "kind": "fixture",
                    "name": "fixture.ready",
                    "source": {"id": "issuer"},
                    "time": {"wall": "2026-01-01T00:00:00.000Z", "elapsed_ms": 0},
                },
                {
                    "kind": "trigger",
                    "name": "trigger.matched",
                    "source": {"id": "revoke-after-2"},
                    "payload": {"occurrence": 2},
                    "time": {"wall": "2026-01-01T00:00:01.000Z", "elapsed_ms": 1000},
                },
                {
                    "kind": "fixture",
                    "name": "fixture.stopped",
                    "source": {"id": "issuer"},
                    "time": {"wall": "2026-01-01T00:00:02.000Z", "elapsed_ms": 2000},
                },
            ]
        },
        title="lanes",
    )
    assert "revoke-after-2" in timed
    assert "issuer" in timed


def test_stream_for_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown"):
        stream_for("loop")


def test_render_html_json_and_svg() -> None:
    plots = TimelinePlots()
    data = {"events": [{"kind": "clock", "name": "clock.started"}]}
    js = render_html("timeline", data, title="t", fmt="json")
    assert "clock" in js
    svg = render_html("timeline", data, fmt="svg")
    assert "svg" in svg
    with pytest.raises(ValueError, match="unknown"):
        render_html("loop", data)
    with pytest.raises(ValueError, match="format"):
        render_html("timeline", data, fmt="pdf")
    embedded = plots.render_embedded("timeline")
    assert embedded["kind"] == "timeline"
    assert "js" in embedded
    assert plots.render_html("timeline", data, fmt="json")
