#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""$path bindings vs free text, and the pipeline context bag."""

from __future__ import annotations

from types import SimpleNamespace

from mas.lab.benchmark.pipeline.binding import (
    as_literal,
    bind_mapping,
    binding_path,
    context_bag,
    fill_scope_columns,
    is_binding,
    pipeline_catalog,
    resolve,
    series_legend,
)
from mas.lab.benchmark.pipeline.figure import FIGURE_KEYS, FigureStep, FigureStyle
from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.resources import ScopeContext


class _Plot(FigureStep):
    HONORS = frozenset(FIGURE_KEYS)

    async def execute(self, ctx):  # noqa: ANN001
        del ctx
        return StepOutput()


def test_dollar_path_is_a_binding_free_text_is_not() -> None:
    assert is_binding("$scenario.metadata.legend")
    assert binding_path("$scenario.metadata.legend") == "scenario.metadata.legend"
    assert not is_binding("scenario.metadata.legend")
    assert not is_binding("skill")
    assert not is_binding("Session trajectories")
    assert not is_binding("$not a path")
    assert not is_binding("$$scenario.metadata.legend")
    assert as_literal("$$price") == "$price"
    assert resolve("$$price", {}) == "$price"


def test_resolve_looks_up_or_returns_literal() -> None:
    bag = {
        "experiment": {"name": "trajectories", "metadata": {"theme": "light"}},
        "scenario": {"id": "full", "metadata": {"legend": "skill"}},
        "test": {"id": "q1", "metadata": {"ci": 0.95}},
    }
    assert resolve("Session trajectories", bag) == "Session trajectories"
    assert resolve("$experiment.name", bag) == "trajectories"
    assert resolve("$scenario.metadata.legend", bag) == "skill"
    assert resolve("$test.metadata.ci", bag) == 0.95
    assert resolve("$scenario.metadata.missing", bag) is None
    assert resolve("$row.value", bag) is None


def test_run_scope_sees_experiment_scenario_test_run() -> None:
    ctx = SimpleNamespace(
        template_vars={
            "experiment": {"id": "exp", "name": "exp", "metadata": {}},
            "scenarios": {"full": {"id": "full", "metadata": {"legend": "skill"}}},
            "tests": {"q1": {"id": "q1", "description": "change of mind"}},
        },
        scope_context=ScopeContext(experiment="exp", scenario="full", test="q1", run="r1"),
    )
    bag = context_bag(ctx)
    assert bag["experiment"]["name"] == "exp"
    assert bag["scenario"]["metadata"]["legend"] == "skill"
    assert bag["test"]["description"] == "change of mind"
    assert bag["run"]["id"] == "r1"


def test_experiment_scope_scenario_is_empty_until_datum() -> None:
    ctx = SimpleNamespace(
        template_vars={
            "experiment": {"id": "exp", "name": "exp"},
            "scenarios": {
                "full": {"id": "full", "metadata": {"legend": "skill"}},
                "empty": {"id": "empty", "metadata": {"legend": "empty skill"}},
            },
            "tests": {},
        },
        scope_context=ScopeContext(experiment="exp"),
    )
    bag = context_bag(ctx)
    assert bag["scenario"] == {}
    assert resolve("$scenario.metadata.legend", bag) is None
    per = context_bag(ctx, {"scenario": bag["scenarios"]["empty"]})
    assert resolve("$scenario.metadata.legend", per) == "empty skill"


def test_standalone_pipeline_same_config_two_experiments() -> None:
    """A pipeline file does not name the experiment; context is injected at run."""
    step = _Plot("plot", {"title": "$experiment.name", "legend": "$scenario.metadata.legend"})
    one = SimpleNamespace(
        template_vars={"experiment": {"name": "alpha"}},
        scope_context=ScopeContext(experiment="alpha"),
    )
    two = SimpleNamespace(
        template_vars={"experiment": {"name": "beta"}},
        scope_context=ScopeContext(experiment="beta"),
    )
    assert step.style_for(one).title == "alpha"
    assert step.style_for(two).title == "beta"
    assert step.style.title == "$experiment.name"


def test_bind_invented_plot_key_and_free_text_legend() -> None:
    step = _Plot("plot", {"ribbon": "$test.metadata.ci", "legend": "skill vs empty"})
    ctx = SimpleNamespace(
        template_vars={"tests": {"q1": {"id": "q1", "metadata": {"ci": 0.8}}}},
        scope_context=ScopeContext(test="q1"),
    )
    assert step.bind(step.config["ribbon"], ctx) == 0.8
    assert step.bind(step.config["legend"], ctx) == "skill vs empty"


def test_series_legend_binding_vs_free_text() -> None:
    catalog = {
        "scenarios": {
            "full": {"id": "full", "metadata": {"legend": "skill"}},
            "empty": {"id": "empty", "metadata": {"legend": "empty skill"}},
        },
        "tests": {"changed-mind": {"id": "changed-mind", "description": "change of mind"}},
    }
    assert series_legend("full/itemchanged-mind", path="$scenario.metadata.legend", catalog=catalog) == "skill"
    assert series_legend("empty", path="$scenario.metadata.legend", catalog=catalog) == "empty skill"
    assert series_legend("full", path="all runs", catalog=catalog) == "all runs"
    assert series_legend("full", path="scenario.metadata.legend", catalog=catalog) == "scenario.metadata.legend"


def test_bind_mapping_adds_columns_from_catalog() -> None:
    bag = {
        "scenarios": {
            "full": {"id": "full", "metadata": {"legend": "skill"}},
            "empty": {"id": "empty", "metadata": {"legend": "empty skill"}},
        },
        "tests": {},
        "runs": {},
    }
    mapping, rows = bind_mapping(
        {"x": "mean", "fill": "$scenario.metadata.legend"},
        [{"scenario": "full", "mean": 1.0}, {"scenario": "empty", "mean": 2.0}],
        bag,
    )
    assert mapping == {"x": "mean", "fill": "scenario_metadata_legend"}
    assert [row["scenario_metadata_legend"] for row in rows] == ["skill", "empty skill"]


def test_gather_copies_missing_scope_ids() -> None:
    ctx = SimpleNamespace(scope_context=ScopeContext(experiment="exp", scenario="full", test="q1"))
    extra = fill_scope_columns(["mean"], ctx)
    assert extra == {"experiment": "exp", "scenario": "full", "test": "q1"}
    assert fill_scope_columns(["scenario", "mean"], ctx) == {"experiment": "exp", "test": "q1"}


def test_pipeline_catalog_from_experiment_object() -> None:
    exp = SimpleNamespace(
        name="trajectories",
        description="one figure",
        metadata={"kind": "print"},
        scenarios=[
            SimpleNamespace(id="full", description="resume", metadata={"legend": "skill"}, tags=["a"]),
        ],
        dataset=None,
    )
    catalog = pipeline_catalog(exp)
    assert catalog["experiment"]["name"] == "trajectories"
    assert catalog["scenarios"]["full"]["metadata"]["legend"] == "skill"


def test_style_for_resolves_labels_map() -> None:
    step = _Plot(
        "plot",
        {"labels": {"title": "$experiment.name", "x": "PC1", "full": "kept"}},
    )
    ctx = SimpleNamespace(
        template_vars={"experiment": {"name": "trajectories"}},
        scope_context=ScopeContext(),
    )
    style = step.style_for(ctx)
    assert style.title == "trajectories"
    assert style.xlabel == "PC1"
    assert style.label("full") == "kept"
    assert FigureStyle.from_config({"title": "literal"}).title == "literal"
