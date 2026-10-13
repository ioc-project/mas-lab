#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from mas.lab.benchmark.schedule.pipeline import materialize_step_dicts
from mas.lab.benchmark.schedule.run_discovery import (
    discover_benchmark_scenarios,
    discover_benchmark_tests,
    list_child_artifact_paths,
)
from mas.lab.lab.config import PipelineStepSpec
from mas.library.lab.steps.data.gather_level import GatherLevelStep
from mas.library.lab.steps.eval.metrics_to_dataframe import (
    MetricsToDataFrameStep,
    rows_from_run_metrics,
)


def _tree(tmp_path: Path) -> Path:
    run = tmp_path / "topo-parallel" / "item1" / "r1"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(
        '{"session": {"goal_success_rate": {"value": 1.0}}}',
        encoding="utf-8",
    )
    (run / "run_info.json").write_text(
        '{"elapsed_ms": 1500, "model": "test", "status": "ok"}',
        encoding="utf-8",
    )
    (run / "data.csv").write_text(
        "scenario,item_id,run_idx,metric,value,latency_s\n"
        "topo-parallel,1,1,goal_success_rate,1.0,1.5\n",
        encoding="utf-8",
    )
    return tmp_path


def test_discover_tests_and_scenarios(tmp_path: Path) -> None:
    _tree(tmp_path)
    tests = discover_benchmark_tests(tmp_path)
    assert len(tests) == 1
    assert tests[0].scenario == "topo-parallel"
    assert tests[0].test == "item1"
    scenarios = discover_benchmark_scenarios(tmp_path)
    assert [s.scenario for s in scenarios] == ["topo-parallel"]


def test_list_child_artifact_paths_is_direct_children_only(tmp_path: Path) -> None:
    _tree(tmp_path)
    run_csvs = list_child_artifact_paths(
        output_dir=tmp_path, scope="test", scenario="topo-parallel", test="item1"
    )
    assert run_csvs == [
        tmp_path / "topo-parallel" / "item1" / "r1" / "data.csv"
    ]
    scenario_csvs = list_child_artifact_paths(
        output_dir=tmp_path, scope="scenario", scenario="topo-parallel"
    )
    assert scenario_csvs == [tmp_path / "topo-parallel" / "item1" / "data.csv"]


def test_scope_run_materializes_per_run_dir(tmp_path: Path) -> None:
    _tree(tmp_path)
    specs = [
        PipelineStepSpec(
            type="eval_mce",
            name="eval-quality",
            phase="post",
            scope="run",
        )
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    assert len(steps) == 1
    assert steps[0]["name"] == "eval-quality-topo-parallel-item1-r1"
    assert steps[0]["config"]["run_dir"] == str(
        (tmp_path / "topo-parallel" / "item1" / "r1").resolve()
    )


def test_test_gather_fans_in_run_df_and_depends_on(tmp_path: Path) -> None:
    _tree(tmp_path)
    specs = [
        PipelineStepSpec(
            type="metrics_to_dataframe",
            name="run-df",
            phase="post",
            scope="run",
            outputs=["df"],
        ),
        PipelineStepSpec(
            type="gather_level",
            name="gather-test",
            phase="post",
            scope="test",
            inputs=["df"],
            outputs=["df"],
            depends_on=["run-df"],
            config={"output": "data.csv"},
        ),
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    by_name = {s["name"]: s for s in steps}
    gather = by_name["gather-test-topo-parallel-item1"]
    assert gather["depends_on"] == ["run-df-topo-parallel-item1-r1"]
    assert gather["config"]["artifact_paths"] == [
        str(tmp_path / "topo-parallel" / "item1" / "r1" / "data.csv")
    ]


def test_application_gather_fans_in_scenario_dfs(tmp_path: Path) -> None:
    for scenario in ("topo-parallel", "topo-linear-pipeline"):
        run = tmp_path / scenario / "item1" / "r1"
        run.mkdir(parents=True)
        (run / "data.csv").write_text(
            f"scenario,item_id,metric,value\n{scenario},1,gsr,1.0\n",
            encoding="utf-8",
        )
        (tmp_path / scenario / "item1" / "data.csv").write_text(
            f"scenario,item_id,metric,value\n{scenario},1,gsr,1.0\n",
            encoding="utf-8",
        )
        (tmp_path / scenario / "data.csv").write_text(
            f"scenario,item_id,metric,value\n{scenario},1,gsr,1.0\n",
            encoding="utf-8",
        )
    specs = [
        PipelineStepSpec(
            type="gather_level",
            name="gather-scenario",
            phase="post",
            scope="scenario",
            inputs=["df"],
            outputs=["df"],
        ),
        PipelineStepSpec(
            type="gather_level",
            name="gather-experiment",
            phase="post",
            scope="application",
            inputs=["df"],
            outputs=["df"],
            depends_on=["gather-scenario"],
            config={"output": "data.csv"},
        ),
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel", "topo-linear-pipeline"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    by_name = {s["name"]: s for s in steps}
    gather = by_name["gather-experiment"]
    scenario_gathers = [
        by_name["gather-scenario-topo-parallel"],
        by_name["gather-scenario-topo-linear-pipeline"],
    ]
    assert set(gather["depends_on"]) == {
        "gather-scenario-topo-parallel",
        "gather-scenario-topo-linear-pipeline",
    }
    instance_paths = {
        path
        for inst in scenario_gathers
        for path in inst["config"].get("artifact_paths") or []
    }
    assert instance_paths == {
        str(tmp_path / "topo-parallel" / "item1" / "data.csv"),
        str(tmp_path / "topo-linear-pipeline" / "item1" / "data.csv"),
    }
    assert set(gather["config"]["artifact_paths"]) == {
        str(tmp_path / "topo-parallel" / "data.csv"),
        str(tmp_path / "topo-linear-pipeline" / "data.csv"),
    }


@pytest.mark.asyncio
async def test_metrics_to_dataframe_single_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "topo-parallel" / "item1" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        '{"session": {"goal_success_rate": {"value": 0.75}}}',
        encoding="utf-8",
    )
    (run_dir / "run_info.json").write_text(
        '{"elapsed_ms": 2000, "status": "ok"}',
        encoding="utf-8",
    )
    rows = rows_from_run_metrics(
        run_dir, scenario="topo-parallel", test="item1", run="r1"
    )
    assert len(rows) == 1
    assert rows[0]["item_id"] == "1"
    assert rows[0]["value"] == 0.75
    assert rows[0]["latency_s"] == 2.0

    step = MetricsToDataFrameStep(
        name="run-df",
        config={
            "run_dir": str(run_dir),
            "scenario": "topo-parallel",
            "test": "item1",
            "run": "r1",
        },
    )

    class _Ctx:
        output_dir = tmp_path
        scope_context = None
        pipeline = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["rows"] == 1
    assert (run_dir / "data.csv").exists()


@pytest.mark.asyncio
async def test_gather_level_concatenates_child_csv(tmp_path: Path) -> None:
    a = tmp_path / "r1" / "data.csv"
    b = tmp_path / "r2" / "data.csv"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    pd.DataFrame([{"metric": "gsr", "value": 1.0}]).to_csv(a, index=False)
    pd.DataFrame([{"metric": "gsr", "value": 0.0}]).to_csv(b, index=False)

    step = GatherLevelStep(
        name="gather-test",
        config={
            "output_dir": str(tmp_path),
            "output": "data.csv",
            "artifact_paths": [str(a), str(b)],
            "annotate": {"item_id": "1"},
        },
    )

    class _Ctx:
        output_dir = tmp_path
        step_outputs = {}
        pipeline = None
        scope_context = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["df"].shape[0] == 2
    assert (tmp_path / "data.csv").exists()


@pytest.mark.asyncio
async def test_gather_level_prefers_in_memory_child_frames(tmp_path: Path) -> None:
    from mas.lab.benchmark.pipeline.models import StepOutput

    stale = tmp_path / "r1" / "data.csv"
    stale.parent.mkdir(parents=True)
    pd.DataFrame([{"metric": "gsr", "value": 0.0}]).to_csv(stale, index=False)

    step = GatherLevelStep(
        name="gather-test",
        depends_on=["run-df-r1"],
        config={
            "output_dir": str(tmp_path),
            "output": "data.csv",
            "artifact_paths": [str(stale)],
        },
    )

    class _Ctx:
        output_dir = tmp_path
        step_outputs = {
            "run-df-r1": StepOutput(data={"df": pd.DataFrame([{"metric": "gsr", "value": 1.0}])}),
        }
        pipeline = None
        scope_context = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert list(out.data["df"]["value"]) == [1.0]


def test_run_df_batches_sibling_runs(tmp_path: Path) -> None:
    for run in ("r1", "r2"):
        d = tmp_path / "topo-parallel" / "item1" / run
        d.mkdir(parents=True)
        (d / "metrics.json").write_text("{}", encoding="utf-8")
    specs = [
        PipelineStepSpec(
            type="metrics_to_dataframe",
            name="run-df",
            phase="post",
            scope="run",
            outputs=["df"],
        )
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    names = {s["name"] for s in steps}
    assert names == {
        "run-df-topo-parallel-item1-r1",
        "run-df-topo-parallel-item1-r2",
    }


def test_eval_mce_batches_sibling_runs(tmp_path: Path) -> None:
    for run in ("r1", "r2"):
        d = tmp_path / "topo-parallel" / "item1" / run
        d.mkdir(parents=True)
        (d / "metrics.json").write_text("{}", encoding="utf-8")
    specs = [
        PipelineStepSpec(
            type="eval_mce",
            name="eval-quality",
            phase="post",
            scope="run",
        )
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    names = {s["name"] for s in steps}
    assert names == {
        "eval-quality-topo-parallel-item1-r1",
        "eval-quality-topo-parallel-item1-r2",
    }


def test_batched_run_df_depends_on_batched_eval(tmp_path: Path) -> None:
    for run in ("r1", "r2"):
        d = tmp_path / "topo-parallel" / "item1" / run
        d.mkdir(parents=True)
        (d / "metrics.json").write_text("{}", encoding="utf-8")
    specs = [
        PipelineStepSpec(
            type="eval_mce",
            name="eval-quality",
            phase="post",
            scope="run",
        ),
        PipelineStepSpec(
            type="metrics_to_dataframe",
            name="run-df",
            phase="post",
            scope="run",
            outputs=["df"],
            depends_on=["eval-quality"],
        ),
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
    )
    by_name = {s["name"]: s for s in steps}
    assert set(by_name) == {
        "eval-quality-topo-parallel-item1-r1",
        "eval-quality-topo-parallel-item1-r2",
        "run-df-topo-parallel-item1-r1",
        "run-df-topo-parallel-item1-r2",
    }
    assert by_name["run-df-topo-parallel-item1-r1"]["depends_on"] == [
        "eval-quality-topo-parallel-item1-r1"
    ]
    assert by_name["run-df-topo-parallel-item1-r2"]["depends_on"] == [
        "eval-quality-topo-parallel-item1-r2"
    ]


@pytest.mark.asyncio
async def test_gather_level_filters_batched_child_frame(tmp_path: Path) -> None:
    from mas.lab.benchmark.pipeline.models import StepOutput

    combined = pd.DataFrame(
        [
            {"scenario": "a", "item_id": "1", "value": 1.0},
            {"scenario": "b", "item_id": "1", "value": 2.0},
        ]
    )
    step = GatherLevelStep(
        name="gather-scenario-a",
        depends_on=["gather-item"],
        config={
            "output_dir": str(tmp_path / "a"),
            "output": "data.csv",
            "scenario": "a",
        },
    )
    (tmp_path / "a").mkdir()

    class _Ctx:
        output_dir = tmp_path
        step_outputs = {"gather-item": StepOutput(data={"df": combined})}
        pipeline = None
        scope_context = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert list(out.data["df"]["scenario"]) == ["a"]
    assert list(out.data["df"]["value"]) == [1.0]


@pytest.mark.asyncio
async def test_eval_mce_skips_using_run_dir_metrics_not_cache(tmp_path: Path) -> None:
    """metrics.json lives in the run folder; resolved cache events must not re-judge."""
    from mas.library.lab.steps.eval.mce import EvalMceStep

    cache_events = tmp_path / "cache" / "abc" / "traces" / "events.jsonl"
    cache_events.parent.mkdir(parents=True)
    cache_events.write_text('{"kind":"test"}\n', encoding="utf-8")

    run_dir = tmp_path / "topo-parallel" / "item1" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        '{"session": {"goal_success_rate": {"value": 1.0}}}',
        encoding="utf-8",
    )
    (run_dir / "traces").mkdir()
    (run_dir / "traces" / "events.jsonl").symlink_to(cache_events)

    step = EvalMceStep(
        name="eval-quality",
        config={
            "run_dir": str(run_dir),
            "events_path": str(cache_events),
            "overwrite": False,
            "scenario": "topo-parallel",
            "test": "item1",
            "run": "r1",
        },
    )

    class _Pipe:
        config_path = tmp_path / "experiment.yaml"

    class _Ctx:
        output_dir = tmp_path
        pipeline = _Pipe()
        scope_context = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["skipped"] == 1
    assert out.data["computed"] == 0
    assert out.files == [run_dir / "metrics.json"]


@pytest.mark.asyncio
async def test_eval_mce_rejects_tree_walk(tmp_path: Path) -> None:
    from mas.library.lab.steps.eval.mce import EvalMceStep

    step = EvalMceStep(name="eval-quality", config={})

    class _Pipe:
        config_path = tmp_path / "experiment.yaml"

    class _Ctx:
        output_dir = tmp_path
        pipeline = _Pipe()
        scope_context = None

    with pytest.raises(RuntimeError, match="run-level events artifact"):
        await step.execute(_Ctx())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_eval_mce_degrades_gracefully_on_scoring_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flaky judge call must not raise and fail the whole (often 100s-of-
    instances) post-phase pipeline — it should degrade this one run instead."""
    from mas.library.lab.steps.eval import mce as mce_module

    run_dir = tmp_path / "topo-parallel" / "item1" / "r1"
    (run_dir / "traces").mkdir(parents=True)
    (run_dir / "traces" / "events.jsonl").write_text('{"kind":"test"}\n', encoding="utf-8")

    def _boom(*args, **kwargs):
        raise RuntimeError("judge LLM proxy timed out")

    # Stock MCE metrics batch through runner.compute_session_metrics.
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.compute_session_metrics", _boom
    )

    step = mce_module.EvalMceStep(
        name="eval-quality",
        config={
            "run_dir": str(run_dir),
            "scenario": "topo-parallel",
            "test": "item1",
            "run": "r1",
        },
    )

    class _Pipe:
        config_path = tmp_path / "experiment.yaml"

    class _Ctx:
        output_dir = tmp_path
        pipeline = _Pipe()
        scope_context = None

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["errors"] == 0
    assert out.data["computed"] == 1
    doc = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert doc["run_quality"]["status"] == "error"
    assert any("judge LLM proxy timed out" in e for e in doc["run_quality"]["errors"])


def test_eval_mce_judge_semaphore_sized_by_max_workers() -> None:
    from mas.library.lab.steps.eval.mce import _judge_semaphore

    sem = _judge_semaphore(3)
    assert sem._value == 3
    # Same size returns the same shared semaphore instance.
    assert _judge_semaphore(3) is sem


def test_gather_level_raises_when_no_artifact_paths_configured(tmp_path: Path) -> None:
    step = GatherLevelStep(
        name="gather-test",
        config={"output_dir": str(tmp_path), "output": "data.csv"},
    )

    class _Ctx:
        output_dir = tmp_path

    with pytest.raises(RuntimeError, match="fan-in produced nothing"):
        import asyncio

        asyncio.run(step.execute(_Ctx()))  # type: ignore[arg-type]


def test_gather_level_raises_when_artifact_paths_all_missing(tmp_path: Path) -> None:
    step = GatherLevelStep(
        name="gather-test",
        config={
            "output_dir": str(tmp_path),
            "output": "data.csv",
            "artifact_paths": [str(tmp_path / "r1" / "data.csv"), str(tmp_path / "r2" / "data.csv")],
        },
    )

    class _Ctx:
        output_dir = tmp_path

    with pytest.raises(RuntimeError, match="none of 2 configured"):
        import asyncio

        asyncio.run(step.execute(_Ctx()))  # type: ignore[arg-type]


def test_effective_scope_prefers_explicit_scope_over_legacy_flags() -> None:
    from mas.lab.benchmark.schedule.pipeline import _effective_scope

    explicit_and_legacy = PipelineStepSpec(
        type="gather_level", name="s", scope="scenario", per_run=True
    )
    assert _effective_scope(explicit_and_legacy) == "scenario"

    legacy_per_run_only = PipelineStepSpec(type="gather_level", name="s", per_run=True)
    assert _effective_scope(legacy_per_run_only) == "run"

    legacy_per_scenario_only = PipelineStepSpec(
        type="gather_level", name="s", per_scenario=True
    )
    assert _effective_scope(legacy_per_scenario_only) == "scenario"

    no_hints = PipelineStepSpec(type="gather_level", name="s")
    assert _effective_scope(no_hints) == "application"


def test_gather_fan_in_honors_custom_level_artifact_path(tmp_path: Path) -> None:
    """A child level's own declared ``path:`` override must win over the
    artifact type's default filename when resolving fan-in."""
    from mas.lab.lab.config.pipeline import ArtifactSpec

    run = tmp_path / "topo-parallel" / "item1" / "r1"
    run.mkdir(parents=True)
    (run / "results.csv").write_text("scenario,value\ntopo-parallel,1\n", encoding="utf-8")

    class _FakeLevel:
        def __init__(self, artifacts):
            self.artifacts = artifacts

    class _FakeExp:
        levels = {
            "run": _FakeLevel(
                [ArtifactSpec(name="df", type="dataframe", path="{level_dir}/results.csv")]
            )
        }

    from mas.lab.benchmark.schedule.pipeline import _level_artifacts

    specs = [
        PipelineStepSpec(
            type="gather_level",
            name="gather-test",
            phase="post",
            scope="test",
            inputs=["df"],
            outputs=["df"],
            config={"output": "results.csv"},
        ),
    ]
    steps = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=["topo-parallel"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(tmp_path)},
        level_artifacts=_level_artifacts(_FakeExp()),
    )
    assert steps[0]["config"]["artifact_paths"] == [str(run / "results.csv")]
