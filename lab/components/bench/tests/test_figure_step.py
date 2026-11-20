#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FigureStep keys, theme defaults, and $path bindings on figure text."""

from __future__ import annotations

import logging

import pytest
from mas.lab.benchmark.pipeline import FigureStep, FigureStyle, StepOutput
from mas.lab.benchmark.pipeline.figure import PALETTES, resolve_palette
from mas.lab.benchmark.pipeline.incremental import folds_batches


class _Full(FigureStep):
    type = "test_full_plot"
    HONORS = frozenset({"theme", "palette", "labels", "title", "subtitle", "xlabel", "ylabel", "caption", "format"})
    FORMATS = ("svg", "html")

    def on_batch(self, batch, ctx):  # noqa: ANN001
        del batch, ctx

    async def execute(self, ctx):  # noqa: ANN001
        return StepOutput()


class _Bare(FigureStep):
    type = "test_bare_plot"
    HONORS = frozenset({"format"})

    async def execute(self, ctx):  # noqa: ANN001
        return StepOutput()


def test_style_defaults_file_formats_are_light_okabe_ito() -> None:
    style = _Full("p", {}).style
    assert style.theme == "light" and style.tokens["bg"] == "#fafafa"
    assert style.colors == PALETTES["okabe-ito"]
    assert style.title is None
    assert style.subtitle is None and style.xlabel is None


def test_style_defaults_html_are_dark_pastel() -> None:
    style = FigureStyle.from_config({"format": "html"})
    assert style.theme == "dark" and style.tokens["bg"] == "#0d1117"
    assert style.colors == PALETTES["pastel"]


def test_style_figure_text_from_keys_or_labels_map() -> None:
    style = FigureStyle.from_config(
        {
            "title": "Session trajectories",
            "subtitle": "One path per conversation",
            "xlabel": "PC1",
            "ylabel": "PC2",
            "caption": "PCA of embeddings",
            "labels": {"full": "procedure delivered", "x": "ignored-if-xlabel-set"},
        }
    )
    assert style.title == "Session trajectories"
    assert style.subtitle == "One path per conversation"
    assert style.xlabel == "PC1"
    assert style.ylabel == "PC2"
    assert style.caption == "PCA of embeddings"
    assert style.label("full") == "procedure delivered"
    inherited = FigureStyle.from_config({"labels": {"title": "From labels", "x": "PC1", "y": "PC2", "full": "A"}})
    assert inherited.title == "From labels"
    assert inherited.xlabel == "PC1" and inherited.ylabel == "PC2"
    assert inherited.label("full") == "A"


def test_style_labels_match_series_or_its_scenario() -> None:
    style = FigureStyle.from_config({"labels": {"full": "procedure delivered"}})
    assert style.label("full") == "procedure delivered"
    assert style.label("full/itemchanged-mind") == "procedure delivered"
    assert style.label("empty") == "empty"


def test_palette_by_name_or_list_and_errors() -> None:
    assert resolve_palette("okabe-ito") == PALETTES["okabe-ito"]
    assert resolve_palette(["#111111", "#222222"]) == ("#111111", "#222222")
    with pytest.raises(ValueError, match="palette"):
        resolve_palette("neon")
    with pytest.raises(ValueError, match="empty"):
        resolve_palette([])
    style = FigureStyle.from_config({"palette": ["#111111", "#222222"]})
    assert style.color(0) == "#111111"
    assert style.color(3) == "#222222"
    with pytest.raises(ValueError, match="labels"):
        FigureStyle.from_config({"labels": ["not", "a", "map"]})
    with pytest.raises(ValueError, match="theme"):
        FigureStyle.from_config({"theme": "sepia"})


def test_unimplemented_figure_key_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        _Bare("bare", {"palette": "pastel"})
    assert "does not implement figure key(s) palette" in caplog.text


def test_unsupported_format_is_an_error() -> None:
    with pytest.raises(ValueError, match="format must be one of"):
        _Full("p", {"format": "png"})


def test_apply_style_fills_honors_from_style_for() -> None:
    step = _Full("p", {"title": "Hello", "format": "svg"})
    bag = step.apply_style(step.config, None)
    assert bag["title"] == "Hello"
    assert bag["theme"] == "light"
    assert list(bag["palette"]) == list(PALETTES["okabe-ito"])


def test_realtime_false_stops_folding() -> None:
    live = _Full("p", {})
    assert folds_batches(live) is True
    barrier = _Full("p", {})
    barrier.realtime = False
    assert folds_batches(barrier) is False


def test_step_yaml_in_out_become_artifacts() -> None:
    from mas.lab.benchmark.pipeline.core import _artifact_names

    assert _artifact_names("metrics") == ("metrics",)
    assert _artifact_names(["metrics", "plot"]) == ("metrics", "plot")
    extract = _Full("stats", {})
    extract.out = ("metrics",)
    arts = extract.output_artifacts
    assert arts[0][0] == "metrics_path"
    plot = _Full("trajectories", {"output": "results/state-embeddings.svg"})
    plot.ins = ("metrics",)
    plot.out = ("plot",)
    assert plot.ins == ("metrics",)
    assert plot.output_artifacts[0][1]._relative_path == "results/state-embeddings.svg"
