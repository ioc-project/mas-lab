#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The file contract between generation, scheduling, execution, retry and analysis."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from mas.lab.benchmark.control import (
    BatchBudget,
    BatchHandle,
    _refresh,
    next_runs,
    pipeline_budget,
)
from mas.lab.benchmark.execution.plan import build_execution_plan
from mas.lab.benchmark.execution.roles import PlannedRun, resolve_role_plugin
from mas.lab.benchmark.execution.wire import (
    RunResult,
    make_shards,
    merge_results,
    plan_document,
    read_plan,
    read_results,
    runs_to_retry,
    select_runs,
    write_plan,
)
from mas.lab.cli.commands.benchmark.runs import runs_group

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def _keep_the_plugin_registry():
    """Planning a real lab registers its libraries; put the registry back for the next test."""
    from mas.runtime.registry import get_registry

    registry = get_registry()
    snapshot = registry.snapshot()
    yield
    registry.restore(snapshot)


def _runs(n_scen: int = 2, n_items: int = 2, runs: int = 1) -> list[PlannedRun]:
    return [PlannedRun(f"s{s}", {"id": f"i{i}"}, r) for s in range(n_scen) for i in range(n_items) for r in range(runs)]


def _write_result(root: Path, run: PlannedRun, status: str, attempts: int = 1, error: str = "") -> None:
    run = root / run.scenario_id / f"item{run.item['id']}" / f"r{run.run_idx + 1}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "result.json").write_text(
        json.dumps({"status": status, "attempts": attempts, "error": error}), encoding="utf-8"
    )


# -- shards ---------------------------------------------------------------------------------------


def test_shard_modes_group_runs() -> None:
    runs = _runs(2, 3)
    assert len(make_shards(runs, by="run")) == 6
    assert [len(g) for g in make_shards(runs, by="scenario")] == [3, 3]
    assert [len(g) for g in make_shards(runs, by="item")] == [2, 2, 2]
    assert [len(g) for g in make_shards(runs, by="chunk", size=4)] == [4, 2]


def test_shards_are_capped_and_lose_no_run() -> None:
    runs = _runs(10, 10)
    shards = make_shards(runs, by="run", max_shards=7)
    assert len(shards) == 7
    assert sorted(c.id for g in shards for c in g) == sorted(c.id for c in runs)


def test_bad_shard_arguments_are_refused() -> None:
    for kwargs in ({"by": "nope"}, {"size": 0}, {"max_shards": 0}):
        with pytest.raises(ValueError):
            make_shards(_runs(), **kwargs)


# -- plan files -----------------------------------------------------------------------------------


def test_plan_roundtrip_and_select(tmp_path: Path) -> None:
    runs = _runs()
    doc = plan_document(
        runs[:2],
        experiment="e",
        experiment_yaml="e.yaml",
        generator="cartesian",
        scheduler="coverage",
        attempts_before={runs[0].id: 2},
    )
    path = write_plan(tmp_path / "p.json", doc)
    chosen, before = select_runs(runs, read_plan(path))
    assert [c.id for c in chosen] == [c.id for c in runs[:2]]
    assert before[runs[0].id] == 2 and before[runs[1].id] == 0


def test_plan_naming_an_ungenerated_run_is_refused(tmp_path: Path) -> None:
    doc = plan_document(_runs(), experiment="e", experiment_yaml="e.yaml", generator="g", scheduler="s")
    with pytest.raises(ValueError, match="does not generate"):
        select_runs(_runs(1, 1), doc)


def test_malformed_plans_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "other"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a"):
        read_plan(bad)
    doc = plan_document(_runs(1, 1), experiment="e", experiment_yaml="e", generator="g", scheduler="s")
    doc["runs"].append(dict(doc["runs"][0]))
    write_plan(bad, doc)
    with pytest.raises(ValueError, match="duplicate"):
        read_plan(bad)


# -- results, merge, retry ------------------------------------------------------------------------


def test_merge_never_replaces_ok_with_failure(tmp_path: Path) -> None:
    a, b, dest = tmp_path / "a", tmp_path / "b", tmp_path / "m"
    run = PlannedRun("s0", {"id": "i0"}, 0)
    _write_result(a, run, "ok")
    _write_result(b, run, "error", error="boom")
    stats = merge_results([a, b], dest)
    assert read_results(dest, [run])[run.id].complete
    assert stats["kept"] == 1
    # later source wins between equals
    _write_result(b, run, "ok", attempts=5)
    merge_results([b], dest)
    assert read_results(dest, [run])[run.id].attempts == 5


def test_merge_follows_trace_cache_links_so_the_tree_stands_alone(tmp_path: Path, monkeypatch) -> None:
    src, dest, cache = tmp_path / "s", tmp_path / "m", tmp_path / "cache"
    monkeypatch.setattr("mas.lab.paths.trace_cache", lambda: cache)
    run = src / "s0" / "itemi0" / "r1"
    (cache / "traces").mkdir(parents=True)
    (cache / "traces" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    run.mkdir(parents=True)
    (run / "traces").symlink_to(cache / "traces")
    (run / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
    merge_results([src], dest)
    target = dest / "s0" / "itemi0" / "r1" / "traces"
    assert not target.is_symlink() and (target / "events.jsonl").is_file()


def test_retry_selection_missing_error_and_give_up() -> None:
    runs = _runs(1, 4)
    results = {
        runs[0].id: RunResult(runs[0].id, "ok", attempts=1),
        runs[1].id: RunResult(runs[1].id, "error", "HTTP 503", attempts=1),
        runs[2].id: RunResult(runs[2].id, "error", "HTTP 503", attempts=3),
        runs[3].id: RunResult(runs[3].id, "missing"),
    }
    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params({"max_attempts": 3, "backoff_s": 0})
    again, spent, gave_up = runs_to_retry(runs, results, retrier)
    assert [c.id for c in again] == [runs[1].id, runs[3].id]
    assert spent == {runs[1].id: 1, runs[3].id: 0}
    assert gave_up == [runs[2].id]


# -- narrowed runs never break a valid design ------------------------------------------------------


def test_factorial_design_survives_a_narrowed_scenario_set() -> None:
    design = {"plugin": "factorial", "params": {"factors": {"a": ["x", "y"], "b": ["p", "q"]}}}
    declared = ["x-p", "x-q", "y-p", "y-q"]
    items = [{"id": 1}]
    plan = build_execution_plan(["x-p", "y-q"], items, 1, design=design, declared_scenario_ids=declared)
    assert [c.scenario_id for c in plan] == ["x-p", "y-q"]
    with pytest.raises(ValueError, match="unknown scenario"):
        build_execution_plan(["x-p"], items, 1, design=design)


# -- the budget is the pipeline's -------------------------------------------------------------------


def test_budget_is_shared_by_every_task_of_a_pipeline() -> None:
    pipeline = SimpleNamespace(config=SimpleNamespace(max_scheduled_batches=3))

    async def one() -> str:
        try:
            pipeline_budget(pipeline).claim()
            return "ok"
        except ValueError:
            return "refused"

    async def main() -> list[str]:
        return await asyncio.gather(*(asyncio.create_task(one()) for _ in range(10)))

    assert asyncio.run(main()).count("ok") == 3
    assert pipeline_budget(pipeline) is pipeline_budget(pipeline)


def test_budget_lowers_but_never_raises() -> None:
    budget = BatchBudget(5)
    budget.lower(2)
    budget.lower(9)
    assert budget.max_scheduled_batches == 2


# -- next_runs -------------------------------------------------------------------------------------


def test_halving_refuses_unscored_scenarios() -> None:
    with pytest.raises(ValueError, match="no score"):
        next_runs(runs=_runs(2, 1), scores={"s0": 1.0}, strategy="successive_halving")
    with pytest.raises(ValueError, match="keep_fraction"):
        next_runs(runs=_runs(2, 1), scores={"s0": 1, "s1": 2}, strategy="successive_halving", keep_fraction=0)


def test_halving_ranks_every_scenario_of_the_batch_not_only_unfinished(tmp_path: Path) -> None:
    runs = _runs(4, 1)
    handle = BatchHandle("b", tmp_path / "e.yaml", tmp_path / "out", runs=runs)
    for run in runs[:3]:  # three scenarios finished; one did not
        _write_result(handle.output_dir, run, "ok")
    scores = {"s0": 0.9, "s1": 0.8, "s2": 0.1, "s3": 0.7}
    kept = next_runs(handle=handle, scores=scores, strategy="successive_halving", keep_n=2)
    assert {c.scenario_id for c in kept} == {"s0", "s1"}
    assert [c.scenario_id for c in next_runs(handle=handle)] == ["s3"]


def test_incomplete_runs_use_the_scenario_directory_name(tmp_path: Path) -> None:
    from mas.lab.benchmark.control import incomplete_runs

    run = PlannedRun("fancy", {"id": "i0"}, 0)
    handle = BatchHandle("b", tmp_path / "e.yaml", tmp_path / "out", runs=[run], dir_names={"fancy": "fancy-dir"})
    run = handle.output_dir / "fancy-dir" / "itemi0" / "r1"
    run.mkdir(parents=True)
    (run / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
    assert incomplete_runs(handle) == []


# -- a batch finishes without a controller ----------------------------------------------------------


class _Proc:
    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def wait(self, timeout=None):
        return self.code


def test_child_process_batch_reaches_a_terminal_status(tmp_path: Path) -> None:
    import tempfile

    ok = BatchHandle("a", tmp_path / "e.yaml", tmp_path / "o", status="running", process=_Proc(0))
    _refresh(ok)
    assert ok.status == "complete"
    bad = BatchHandle("b", tmp_path / "e.yaml", tmp_path / "o", status="running", process=_Proc(2))
    bad.stderr_file = tempfile.TemporaryFile()
    bad.stderr_file.write(b"Traceback: boom")
    _refresh(bad)
    assert bad.status == "error" and "boom" in bad.error
    waiting = BatchHandle("c", tmp_path / "e.yaml", tmp_path / "o", status="running", process=_Proc(None))
    _refresh(waiting)
    assert waiting.status == "running"


# -- CLI: the whole round trip, every step its own process-sized call --------------------------------


def _experiment(tmp: Path, retry: str = "") -> Path:
    (tmp / "mas.yaml").write_text("kind: Agent\nmetadata: {name: dummy}\n", encoding="utf-8")
    (tmp / "dataset.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d}\nspec:\n  items:\n"
        "    - {id: i0, inputs: {user: a}}\n    - {id: i1, inputs: {user: b}}\n",
        encoding="utf-8",
    )
    path = tmp / "experiment.yaml"
    path.write_text(
        "experiment:\n  name: ci\n  application:\n    manifest: ./mas.yaml\n  scenarios:\n"
        "    - id: s0\n    - id: s1\n  dataset:\n    path: ./dataset.yaml\n  run:\n    n_runs: 1\n" + retry,
        encoding="utf-8",
    )
    return path


def test_runs_cli_plan_merge_retry_summary(tmp_path: Path) -> None:
    exp = _experiment(tmp_path, retry="  retry:\n    params:\n      max_attempts: 2\n      backoff_s: 0\n")
    runner = CliRunner()
    plan = tmp_path / "plan" / "plan.json"
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("experiment:\n  run:\n    n_runs: 1\n", encoding="utf-8")
    out = runner.invoke(runs_group, ["plan", str(exp), "-o", str(plan), "--shard-by", "scenario", "-x", str(overlay)])
    assert out.exit_code == 0, out.output
    matrix = json.loads((plan.parent / "matrix.json").read_text(encoding="utf-8"))
    assert [row["n_runs"] for row in matrix["include"]] == [2, 2]
    shard = read_plan(plan.parent / "shard-0.json")
    assert {row["scenario"] for row in shard["runs"]} == {"s0"}

    runs = [PlannedRun(s, {"id": i}, 0) for s in ("s0", "s1") for i in ("i0", "i1")]
    shard_a, shard_b = tmp_path / "out-0", tmp_path / "out-1"
    for run in runs[:2]:
        _write_result(shard_a, run, "ok")
    _write_result(shard_b, runs[2], "ok")
    _write_result(shard_b, runs[3], "error", attempts=1, error="LLM request failed: HTTP 503")
    merged = tmp_path / "merged"
    assert runner.invoke(runs_group, ["merge", str(shard_a), str(shard_b), "-o", str(merged)]).exit_code == 0

    retry_path = tmp_path / "retry" / "retry-plan.json"
    out = runner.invoke(
        runs_group, ["retry", str(exp), "--plan", str(plan), "--results", str(merged), "-o", str(retry_path)]
    )
    assert out.exit_code == 0, out.output
    retry = read_plan(retry_path)
    assert [row["id"] for row in retry["runs"]] == [runs[3].id]
    assert retry["runs"][0]["attempts_before"] == 1

    summary = runner.invoke(
        runs_group, ["summary", str(exp), "--plan", str(plan), "--results", str(merged), "--fail-on-incomplete"]
    )
    assert summary.exit_code == 1
    report = json.loads(summary.output)
    assert report["incomplete"] == [runs[3].id] and report["complete"] == 3
    assert (merged / "analysis" / "summary.json").is_file()

    _write_result(merged, runs[3], "ok", attempts=2)
    done = runner.invoke(
        runs_group, ["summary", str(exp), "--plan", str(plan), "--results", str(merged), "--fail-on-incomplete"]
    )
    assert done.exit_code == 0


def test_retry_gives_up_after_the_cap_across_rounds(tmp_path: Path) -> None:
    exp = _experiment(tmp_path, retry="  retry:\n    params:\n      max_attempts: 2\n      backoff_s: 0\n")
    runner = CliRunner()
    plan = tmp_path / "plan.json"
    runner.invoke(runs_group, ["plan", str(exp), "-o", str(plan)])
    merged = tmp_path / "merged"
    _write_result(merged, PlannedRun("s0", {"id": "i0"}, 0), "error", attempts=2, error="HTTP 503")
    out = runner.invoke(
        runs_group, ["retry", str(exp), "--plan", str(plan), "--results", str(merged), "-o", str(tmp_path / "r.json")]
    )
    doc = read_plan(tmp_path / "r.json")
    assert (PlannedRun("s0", {"id": "i0"}, 0)).id in doc["gave_up"]
    assert out.exit_code == 0


def test_run_selects_only_the_runs_of_a_plan_file(tmp_path: Path) -> None:
    from mas.lab.benchmark.execution.planner import plan_experiment

    exp = _experiment(tmp_path)
    _, runs = plan_experiment(exp)
    doc = plan_document(
        runs[:1], experiment="ci", experiment_yaml=str(exp), generator="cartesian", scheduler="coverage"
    )
    chosen, _ = select_runs(runs, doc)
    assert [c.id for c in chosen] == [runs[0].id]


def test_sample_lab_pipeline_writes_a_runnable_round_two_plan(tmp_path: Path) -> None:
    from mas.lab.benchmark.execution.planner import plan_experiment
    from mas.lab.benchmark.pipeline.executor import ExecutionContext
    from mas.library.lab.steps.control.next_runs import NextRunsStep

    lab = ROOT / "labs" / "factorial-halving.lab"
    step = NextRunsStep(
        "keep",
        {
            "strategy": "successive_halving",
            "keep_fraction": 0.5,
            "experiment": "./experiment.yaml",
            "scores_path": "./fixtures/round1-scores.json",
            "plan_path": str(tmp_path / "round2.json"),
        },
    )
    ctx = ExecutionContext(
        pipeline=SimpleNamespace(config_path=lab / "pipeline.yaml", name="p"),
        output_dir=tmp_path,
        cache_manager=SimpleNamespace(),
    )
    out = asyncio.run(step.execute(ctx))
    assert {c.scenario_id for c in out.data["runs"]} == {"small-short", "small-long"}
    _, runs = plan_experiment(lab / "experiment.yaml")
    chosen, _ = select_runs(runs, read_plan(out.data["plan_file"]))
    assert {c.scenario_id for c in chosen} == {"small-short", "small-long"}


def test_run_cmd_has_runs_and_no_strategy() -> None:
    from mas.lab.cli.commands.benchmark.run import run_cmd

    names = {opt for p in run_cmd.params for opt in p.opts}
    assert "--run-plan" in names and "--strategy" not in names


# -- hostile inputs: files that came from another machine -----------------------------------------


def test_merge_skips_links_that_leave_the_tree(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not copy", encoding="utf-8")
    src, dest = tmp_path / "s", tmp_path / "m"
    run = src / "s0" / "itemi0" / "r1"
    run.mkdir(parents=True)
    (run / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
    (run / "leak.txt").symlink_to(secret)
    merge_results([src], dest)
    assert (dest / "s0" / "itemi0" / "r1" / "result.json").is_file()
    assert not (dest / "s0" / "itemi0" / "r1" / "leak.txt").exists()


def test_run_ref_cannot_name_a_path_outside_the_trace_cache(tmp_path: Path) -> None:
    from mas.lab.benchmark.execution.completeness import run_dir_is_complete
    from mas.lab.benchmark.execution.wire import read_result

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / ".run_ref").write_text(f"../../{outside}", encoding="utf-8")
    assert read_result(run) is None
    assert not run_dir_is_complete(run)


def test_hostile_attempt_counts_are_clamped(tmp_path: Path) -> None:
    run = PlannedRun("s0", {"id": "i0"}, 0)
    folder = tmp_path / "s0" / "itemi0" / "r1"
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(
        '{"status": "error", "attempts": 1000000000000, "error": "x"}', encoding="utf-8"
    )
    assert read_results(tmp_path, [run])[run.id].attempts == 1000
    doc = plan_document(
        [run],
        experiment="e",
        experiment_yaml="e",
        generator="g",
        scheduler="s",
        attempts_before={run.id: 10**12},
    )
    assert select_runs([run], doc)[1][run.id] == 1000
    out = asyncio.run(_one_attempt(10**12))
    assert out.attempts == 1001


async def _one_attempt(before: int):
    from mas.lab.benchmark.execution.retry import run_with_retrier
    from mas.lab.benchmark.execution.roles import resolve_executor

    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params({"max_attempts": 2, "backoff_s": 0})
    return await run_with_retrier(
        resolve_executor("native"), retrier, lambda: {"status": "ok", "content": "x"}, attempts_before=before
    )


def test_a_batch_that_schedules_a_batch_stops_at_the_depth_limit(tmp_path: Path) -> None:
    from mas.lab.benchmark.control import (
        DEPTH_MARKER,
        MAX_BATCH_DEPTH,
        batch_depth,
        reset_batches,
        schedule_batch,
        set_batch_runner,
    )

    exp = _experiment(tmp_path)
    set_batch_runner(lambda handle: setattr(handle, "status", "complete"))
    try:
        first = schedule_batch(exp)
        assert batch_depth(first.output_dir) == 1
        inner = first.output_dir
        for level in range(1, MAX_BATCH_DEPTH):
            child = schedule_batch(exp, lineage_dir=inner)
            assert batch_depth(child.output_dir) == level + 1
            inner = child.output_dir
        with pytest.raises(ValueError, match="deep"):
            schedule_batch(exp, lineage_dir=inner)
        (inner / DEPTH_MARKER).write_text("not a number", encoding="utf-8")
        assert batch_depth(inner) == MAX_BATCH_DEPTH
    finally:
        reset_batches()
