#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the shared custom.{owner}.plot envelope helper, verified against
the real client-side contract (demo-ui/src/plugins/html-plot.js's chartOf())."""

import base64

import pytest

from mas.library.lab.steps.services.plot_stream import (
    build_plot_record,
    image_html_shell,
    post_plot_tick,
)


def test_build_plot_record_shape_matches_html_plot_js_contract():
    record = build_plot_record("ci_plot", kind="image", title="CI figure", html="<html>x</html>")
    assert record["kind"] == "custom.ci_plot.plot"
    assert record["plot_kind"] == "image"
    assert record["title"] == "CI figure"
    assert record["html"] == "<html>x</html>"
    assert "data" not in record


def test_build_plot_record_accepts_data_only():
    record = build_plot_record("ci_plot", kind="table", data={"rows": [1, 2, 3]})
    assert record["data"] == {"rows": [1, 2, 3]}


def test_build_plot_record_requires_owner():
    with pytest.raises(ValueError, match="owner"):
        build_plot_record("", kind="image", html="<html></html>")


def test_build_plot_record_rejects_content_free_tick():
    # Neither html nor data -- would publish but render nothing. This is the
    # real bug build_plot_record exists to catch (see its docstring: "kind"
    # alone would satisfy html-plot.js's chartOf(), so a naive truthy check
    # against its full field list would never fire).
    with pytest.raises(ValueError, match="neither 'data' nor 'html'"):
        build_plot_record("ci_plot", kind="image")


def test_image_html_shell_embeds_real_png_bytes(tmp_path):
    png_path = tmp_path / "figure.png"
    # Minimal valid 1x1 PNG.
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    png_path.write_bytes(png_bytes)
    html = image_html_shell(png_path, title="My Figure")
    assert html.startswith("<!doctype html>")
    assert "image/png" in html
    assert base64.b64encode(png_bytes).decode("ascii") in html
    assert "My Figure" in html


def test_image_html_shell_escapes_title():
    html = image_html_shell(__file__ and _fake_png(), title="<script>evil</script>")
    assert "<script>evil</script>" not in html
    assert "&lt;script&gt;" in html


def _fake_png():
    import base64
    import tempfile
    from pathlib import Path

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    tmp = Path(tempfile.mkstemp(suffix=".png")[1])
    tmp.write_bytes(png_bytes)
    return tmp


def test_post_plot_tick_noop_without_ingest_url(monkeypatch):
    monkeypatch.delenv("MAS_PIPELINE_INGEST_URL", raising=False)
    # Must not raise even with no ingest URL configured anywhere.
    post_plot_tick("ci_plot", kind="image", html="<html></html>")


def test_post_plot_tick_posts_built_record(monkeypatch):
    posted = {}

    def fake_post(record, *, ref=None):
        posted["record"] = record
        posted["ref"] = ref

    monkeypatch.setattr("mas.library.lab.steps.services.plot_stream.publish_custom_record", fake_post)
    post_plot_tick("ci_plot", kind="image", title="t", html="<h></h>", ref={"scenario": "s"})
    assert posted["record"]["kind"] == "custom.ci_plot.plot"
    assert posted["ref"] == {"scenario": "s"}
