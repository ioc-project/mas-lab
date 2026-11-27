#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The builtin role plugins, one by one: generators, schedulers, executor, retrier, analyzer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mas.lab.benchmark.execution.roles import (
    AnalysisContext,
    AttemptRecord,
    DesignContext,
    PlannedRun,
    ScheduleContext,
)
from mas.library.lab.plugins.analyzers.native import NativeAnalyzer
from mas.library.lab.plugins.executors.native import NativeExecutor
from mas.library.lab.plugins.retriers.native import NativeRetrier
from mas.library.lab.plugins.scenario_generators.cartesian import CartesianGenerator
from mas.library.lab.plugins.scenario_generators.coupled import CoupledGenerator
from mas.library.lab.plugins.scenario_generators.factorial import FactorialGenerator
from mas.library.lab.plugins.scenario_generators.one_factor import OneFactorGenerator
from mas.library.lab.plugins.schedulers.as_generated import AsGeneratedScheduler
from mas.library.lab.plugins.schedulers.coverage import CoverageScheduler
from mas.library.lab.plugins.schedulers.depth import DepthScheduler

ITEMS = [{"id": "a"}, {"id": "b"}]


def _ctx(scenarios=("s1", "s2"), runs=2, **params) -> DesignContext:
    return DesignContext(list(scenarios), ITEMS, runs, params)


def _ids(runs: list[PlannedRun]) -> list[tuple[str, str, int]]:
    return [(c.scenario_id, c.item_id, c.run_idx) for c in runs]


# -- generators -----------------------------------------------------------------------------------


def test_no_param_plugins_refuse_params() -> None:
    for plugin in (
        CartesianGenerator(),
        CoverageScheduler(),
        DepthScheduler(),
        AsGeneratedScheduler(),
        NativeExecutor(),
        NativeAnalyzer(),
    ):
        plugin.validate_params({})
        with pytest.raises(ValueError, match="unknown key"):
            plugin.validate_params({"x": 1})


def test_cartesian_is_scenario_item_run() -> None:
    runs = CartesianGenerator().generate(_ctx(runs=1))
    assert _ids(runs) == [("s1", "a", 0), ("s1", "b", 0), ("s2", "a", 0), ("s2", "b", 0)]


def test_coupled_lists_pairs_and_checks_params() -> None:
    gen = CoupledGenerator()
    for bad in ({}, {"couplings": []}, {"couplings": [], "x": 1}):
        with pytest.raises(ValueError):
            gen.validate_params(bad)
    params = {"couplings": [{"scenario": "s1", "items": ["b"]}, {"scenario": "s2"}]}
    gen.validate_params(params)
    assert _ids(gen.generate(_ctx(runs=1, **params))) == [("s1", "b", 0), ("s2", "a", 0), ("s2", "b", 0)]
    with pytest.raises(ValueError, match="missing scenario"):
        gen.generate(_ctx(couplings=[{"items": ["a"]}]))
    with pytest.raises(ValueError, match="unknown item"):
        gen.generate(_ctx(couplings=[{"scenario": "s1", "items": ["zz"]}]))


def test_one_factor_sweeps_one_axis() -> None:
    gen = OneFactorGenerator()
    with pytest.raises(ValueError, match="one_factor"):
        gen.validate_params({"vary": {"axis": "run", "values": [1]}})
    by_scenario = {"vary": {"axis": "scenario", "values": ["s2"]}, "pin_items": ["a"]}
    gen.validate_params(by_scenario)
    assert _ids(gen.generate(_ctx(runs=1, **by_scenario))) == [("s2", "a", 0)]
    by_item = {"vary": {"axis": "item", "values": ["b"]}, "pin_scenarios": ["s1"]}
    assert _ids(gen.generate(_ctx(runs=1, **by_item))) == [("s1", "b", 0)]


def test_factorial_selects_declared_scenarios_and_excludes() -> None:
    gen = FactorialGenerator()
    declared = ("x-p", "x-q", "y-p", "y-q")
    params = {"factors": {"k": ["x", "y"], "m": ["p", "q"]}, "exclude": [{"k": "y", "m": "q"}, "x-q"]}
    gen.validate_params(params)
    runs = gen.generate(_ctx(scenarios=declared, runs=1, **params))
    assert {c.scenario_id for c in runs} == {"x-p", "y-p"}
    for bad in ({}, {"factors": {"k": []}}, {"factors": {"": [1]}}, {"factors": {"k": [1]}, "exclude": 3}):
        with pytest.raises(ValueError):
            gen.validate_params(bad)
    with pytest.raises(ValueError, match="exclude entries"):
        gen.generate(_ctx(scenarios=declared, factors={"k": ["x"]}, exclude=[7]))
    with pytest.raises(ValueError, match="unknown scenario"):
        gen.generate(_ctx(scenarios=("x-p",), factors={"k": ["x", "y"], "m": ["p"]}))


# -- schedulers -----------------------------------------------------------------------------------


def test_schedulers_order_the_same_runs_three_ways() -> None:
    runs = CartesianGenerator().generate(_ctx(runs=2))
    ctx = ScheduleContext(scenario_ids=["s1", "s2"])
    assert AsGeneratedScheduler().order(runs, ctx) == runs
    coverage = CoverageScheduler().order(runs, ctx)
    assert [c.run_idx for c in coverage] == [0, 0, 0, 0, 1, 1, 1, 1]
    depth = DepthScheduler().order(runs, ctx)
    assert _ids(depth)[:2] == [("s1", "a", 0), ("s1", "a", 1)]


# -- executor -------------------------------------------------------------------------------------


def test_executor_runs_once_and_reports_each_way_a_run_ends() -> None:
    plugin = NativeExecutor()
    ok = asyncio.run(plugin.attempt(lambda: {"status": "ok", "content": "done"}))
    assert (ok.status, ok.output, ok.fatal) == ("ok", "done", False)
    assert plugin.is_complete({}, status="ok") and not plugin.is_complete({}, status="error")

    def boom() -> dict:
        raise ConnectionError("down")

    failed = asyncio.run(plugin.attempt(boom))
    assert (failed.status, failed.error) == ("error", "down")

    async def broken() -> None:
        raise RuntimeError("pre failed")

    pre = asyncio.run(plugin.attempt(lambda: {"status": "ok"}, before_attempt=broken))
    assert pre.fatal and "pre failed" in pre.error

    import time

    plugin.timeout_s = 0.05

    def hang() -> dict:
        time.sleep(0.4)
        return {"status": "ok", "content": "late"}

    timed_out = asyncio.run(plugin.attempt(hang))
    assert timed_out.status == "error" and "timed out" in timed_out.error


# -- retrier --------------------------------------------------------------------------------------


def _retrier(**params) -> NativeRetrier:
    retrier = NativeRetrier()
    retrier.validate_params(params)
    return retrier


def test_retry_params_are_validated() -> None:
    for bad in ({"max_attempts": 0}, {"max_attempts": True}, {"backoff_s": -1}, {"backoff_s": "x"}, {"bogus": 1}):
        with pytest.raises(ValueError):
            NativeRetrier().validate_params(bad)


def test_retrier_is_pure_and_honours_retry_after() -> None:
    retrier = _retrier(max_attempts=3, backoff_s=1)
    history = [AttemptRecord("error", "HTTP 503")]
    first = retrier.decide(history)
    assert first.retry and first.delay_s == 1.0
    assert retrier.decide(history) == first
    assert retrier.decide([AttemptRecord("error", "429 retry-after: 7")]).delay_s == 7.0
    assert not retrier.decide(history * 3).retry
    assert not retrier.decide([AttemptRecord("error", "HTTP 401 (authentication/authorization)")]).retry
    assert not retrier.decide([AttemptRecord("error", "x", fatal=True)]).retry
    assert not retrier.decide([AttemptRecord("ok")]).retry
    assert not retrier.decide([]).retry


# -- analyzer -------------------------------------------------------------------------------------


def test_analyzer_counts_complete_error_and_missing_runs_per_scenario(tmp_path: Path) -> None:
    runs = [PlannedRun("s1", {"id": "a"}, 0), PlannedRun("s1", {"id": "b"}, 0), PlannedRun("s2", {"id": "a"}, 0)]
    for run, status in ((runs[0], "ok"), (runs[1], "error")):
        run = tmp_path / run.scenario_id / f"item{run.item_id}" / "r1"
        run.mkdir(parents=True)
        (run / "result.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    report = NativeAnalyzer().analyze(AnalysisContext(results_root=tmp_path, runs=runs))
    assert report["planned"] == 3 and report["complete"] == 1
    assert report["scenarios"] == {
        "s1": {"planned": 2, "complete": 1, "error": 1, "missing": 0},
        "s2": {"planned": 1, "complete": 0, "error": 0, "missing": 1},
    }
    assert report["incomplete"] == sorted([runs[1].id, runs[2].id])
    assert json.loads((tmp_path / "analysis" / "summary.json").read_text(encoding="utf-8")) == report
