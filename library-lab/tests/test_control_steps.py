#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control steps: schedule_experiment, next_runs, propose_prompts, inject_experiment."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from mas.lab.benchmark.control import (
    reset_batches,
    schedule_batch,
    set_batch_runner,
)
from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import StepOutput
from mas.library.lab.steps.control.next_runs import NextRunsStep
from mas.library.lab.steps.control.schedule_experiment import ScheduleExperimentStep

DATASET = (
    "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d}\nspec:\n  items:\n    - {id: q1, inputs: {user: hello}}\n"
)


def _experiment(tmp_path: Path, name: str, scenarios: str = "    - id: s1\n") -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        f"experiment:\n  name: {name}\n  application:\n    manifest: ./mas.yaml\n"
        f"  scenarios:\n{scenarios}  dataset:\n    path: ./dataset.yaml\n  run:\n    n_runs: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "dataset.yaml").write_text(DATASET, encoding="utf-8")
    (tmp_path / "mas.yaml").write_text("kind: Agent\nmetadata: {name: dummy}\n", encoding="utf-8")
    return path


def _item(n: int) -> dict:
    return {"id": n, "prompt": f"q{n}"}


@pytest.mark.asyncio
async def test_schedule_experiment_step_calls_api(tmp_path: Path) -> None:
    yaml_path = _experiment(tmp_path, "t")

    def runner(handle) -> None:  # noqa: ANN001
        handle.status = "complete"

    set_batch_runner(runner)
    try:
        step = ScheduleExperimentStep("round-1", {"experiment": str(yaml_path), "max_scheduled_batches": 2})
        pipeline = SimpleNamespace(config_path=yaml_path, name="p")
        ctx = ExecutionContext(
            pipeline=pipeline,
            output_dir=tmp_path,
            cache_manager=SimpleNamespace(),
        )
        out = await step.execute(ctx)
        assert out.data["batch_id"]
        assert out.data["batch"]["status"] == "complete"
    finally:
        reset_batches()


@pytest.mark.asyncio
async def test_next_runs_step_reads_scores_file(tmp_path: Path) -> None:
    scores = tmp_path / "scores.json"
    scores.write_text('{"keep": 0.9, "drop": 0.1}', encoding="utf-8")
    step = NextRunsStep(
        "halve",
        {
            "strategy": "successive_halving",
            "keep_fraction": 0.5,
            "scores_path": str(scores),
        },
    )
    step.depends_on = ["wait"]
    pipeline = SimpleNamespace(config_path=scores, name="p")
    ctx = ExecutionContext(pipeline=pipeline, output_dir=tmp_path, cache_manager=SimpleNamespace())
    ctx.step_outputs["wait"] = StepOutput(
        data={"runs": [PlannedRun("keep", _item(1), 0), PlannedRun("drop", _item(1), 0)]}
    )
    out = await step.execute(ctx)
    assert [c.scenario_id for c in out.data["runs"]] == ["keep"]


@pytest.mark.asyncio
async def test_schedule_experiment_uses_upstream_injected_yaml(tmp_path: Path) -> None:
    from mas.library.lab.steps.control.inject_experiment import InjectExperimentStep
    from mas.library.lab.steps.control.schedule_experiment import ScheduleExperimentStep

    template = tmp_path / "round.yaml"
    template.write_text(
        "experiment:\n"
        "  name: seed\n"
        "  application:\n"
        "    manifest: ./agent.yaml\n"
        "  scenarios:\n"
        "    - id: baseline\n"
        "  run:\n"
        "    n_runs: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "agent.yaml").write_text("kind: Agent\nmetadata: {name: dummy}\n", encoding="utf-8")
    seen: list[str] = []

    def runner(handle) -> None:  # noqa: ANN001
        seen.append(str(handle.experiment_yaml))
        handle.status = "complete"

    set_batch_runner(runner)
    pipeline = SimpleNamespace(config_path=template, name="p")
    ctx = ExecutionContext(pipeline=pipeline, output_dir=tmp_path, cache_manager=SimpleNamespace())
    ctx.step_outputs["propose"] = StepOutput(
        data={"prompts": [{"id": "next-1", "user_prompt": "Plan a cheaper weekend in Portalis."}]}
    )
    try:
        inject = InjectExperimentStep("inject", {"experiment": str(template)})
        inject.depends_on = ["propose"]
        injected = await inject.execute(ctx)
        ctx.step_outputs["inject"] = injected
        step = ScheduleExperimentStep("round-2", {"max_scheduled_batches": 2})
        step.depends_on = ["inject"]
        out = await step.execute(ctx)
        assert Path(injected.data["experiment_yaml"]).is_file()
        assert out.data["batch"]["status"] == "complete"
        assert seen and Path(seen[0]).resolve() == Path(injected.data["experiment_yaml"]).resolve()
        assert out.data["batch_id"]
        dataset = yaml.safe_load(Path(injected.data["dataset_yaml"]).read_text(encoding="utf-8"))
        assert dataset["spec"]["items"][0]["inputs"]["user"] == "Plan a cheaper weekend in Portalis."
    finally:
        reset_batches()


@pytest.mark.asyncio
async def test_propose_prompts_fixture_skips_judge(tmp_path: Path) -> None:
    from mas.library.lab.steps.control.propose_prompts import ProposePromptsStep

    prompts = tmp_path / "next-prompts.json"
    prompts.write_text(
        '[{"id": "next-1", "user_prompt": "Ask for indoor attractions only.", "parent_id": "a"}]',
        encoding="utf-8",
    )
    step = ProposePromptsStep("propose", {"prompts_path": str(prompts), "n": 1})
    pipeline = SimpleNamespace(config_path=prompts, name="p")
    ctx = ExecutionContext(pipeline=pipeline, output_dir=tmp_path, cache_manager=SimpleNamespace())
    out = await step.execute(ctx)
    assert out.data["source"] == "fixture"
    assert out.data["prompts"][0]["user_prompt"] == "Ask for indoor attractions only."


@pytest.mark.asyncio
async def test_propose_prompts_judge_skips_incomplete_traces(tmp_path: Path) -> None:
    from mas.library.lab.steps.control.propose_prompts import ProposePromptsStep, set_prompt_completer

    yaml_path = _experiment(tmp_path, "traces", "    - id: baseline\n")
    captured: list[str] = []

    def completer(messages, **_kwargs):  # noqa: ANN001
        captured.append(messages[-1]["content"])
        return {"prompts": [{"id": "next-1", "user_prompt": "Rewrite the request more narrowly."}]}

    def runner(handle) -> None:  # noqa: ANN001
        handle.status = "complete"

    set_batch_runner(runner)
    set_prompt_completer(completer)
    try:
        handle = schedule_batch(yaml_path)
        handle.output_dir = tmp_path / "runs"
        complete = PlannedRun("baseline", {"id": "keep", "inputs": {"user": "Plan a weekend."}}, 0)
        broken = PlannedRun("baseline", {"id": "drop", "inputs": {"user": "Skip this one."}}, 0)
        handle.runs = [complete, broken]
        ok_dir = handle.output_dir / "baseline" / "itemkeep" / "r1"
        bad_dir = handle.output_dir / "baseline" / "itemdrop" / "r1"
        (ok_dir / "traces").mkdir(parents=True)
        (bad_dir / "traces").mkdir(parents=True)
        (ok_dir / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
        (ok_dir / "traces" / "events.jsonl").write_text(
            '{"kind": "execution_end", "output": "ok-complete"}\n', encoding="utf-8"
        )
        (bad_dir / "result.json").write_text('{"status": "error"}', encoding="utf-8")
        (bad_dir / "traces" / "events.jsonl").write_text(
            '{"kind": "execution_end", "output": "SECRET_INCOMPLETE"}\n', encoding="utf-8"
        )
        step = ProposePromptsStep("propose", {"n": 1, "batch_id": handle.batch_id, "evidence": "trajectory"})
        step.depends_on = ["wait"]
        pipeline = SimpleNamespace(config_path=yaml_path, name="p")
        ctx = ExecutionContext(pipeline=pipeline, output_dir=tmp_path, cache_manager=SimpleNamespace())
        ctx.step_outputs["wait"] = StepOutput(
            data={"runs": [complete, broken], "scores": {"keep": 0.9, "drop": 0.1}, "batch_id": handle.batch_id}
        )
        out = await step.execute(ctx)
        assert out.data["source"] == "judge"
        assert out.data["prompts"][0]["id"] == "next-1"
        assert captured and "ok-complete" in captured[0]
        assert "SECRET_INCOMPLETE" not in captured[0]
    finally:
        set_prompt_completer(None)
        reset_batches()


# -- helpers and the remaining steps --------------------------------------------------------------


def test_item_user_prompt_reads_every_shape() -> None:
    from mas.library.lab.steps.control._util import item_user_prompt

    assert item_user_prompt("not a dict") == ""
    assert item_user_prompt({"inputs": {"user": "hi"}}) == "hi"
    assert item_user_prompt({"inputs": {"user": ["first", "second"]}}) == "first"
    assert item_user_prompt({"inputs": {"user": [{"content": "from a part"}]}}) == "from a part"
    assert item_user_prompt({"query": "flat"}) == "flat"
    assert item_user_prompt({"inputs": {"user": ["  "]}}) == ""


def _ctx(tmp_path: Path, **outputs) -> ExecutionContext:
    ctx = ExecutionContext(
        pipeline=SimpleNamespace(config_path=tmp_path / "p.yaml", name="p"),
        output_dir=tmp_path,
        cache_manager=SimpleNamespace(),
    )
    for name, data in outputs.items():
        ctx.step_outputs[name] = StepOutput(data=data)
    return ctx


def test_load_scores_from_a_file_a_table_and_a_mapping(tmp_path: Path) -> None:
    from mas.library.lab.steps.control._util import load_scores

    step = SimpleNamespace(name="s", config={"scores_path": "scores.json"}, depends_on=[])
    (tmp_path / "scores.json").write_text('{"a": 1}', encoding="utf-8")
    assert load_scores(step, _ctx(tmp_path)).scenarios == {"a": 1.0}
    (tmp_path / "scores.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_scores(step, _ctx(tmp_path))

    table = SimpleNamespace(name="t", config={"score_key": "v"}, depends_on=["up", "ghost", "odd"])
    ctx = _ctx(
        tmp_path,
        up={"scores": {"x": 2}, "rows": "ignored"},
        odd={"rows": "not a list"},
    )
    ctx.step_outputs["blank"] = None
    assert load_scores(table, ctx).scenarios == {"x": 2.0}
    rows = SimpleNamespace(name="r", config={"score_key": "v"}, depends_on=["rows"])
    scored = load_scores(
        rows, _ctx(tmp_path, rows={"rows": [{"scenario": "s", "item": "i", "v": 3}, "junk", {"v": 1}]})
    )
    assert scored.scenarios == {"s": 3.0} and scored.items == {"i": 3.0}


@pytest.mark.asyncio
async def test_await_batch_waits_for_a_terminal_status_and_times_out(tmp_path: Path) -> None:
    from mas.lab.benchmark.control import reset_batches, schedule_batch, set_batch_runner
    from mas.library.lab.steps.control.await_batch import AwaitBatchStep

    yaml_path = _experiment(tmp_path, "waits")
    set_batch_runner(lambda handle: setattr(handle, "status", "running"))
    try:
        handle = schedule_batch(yaml_path)
        ctx = _ctx(tmp_path, scheduled={"batch_id": handle.batch_id})
        stuck = AwaitBatchStep("wait", {"timeout_s": 0.05, "poll_s": 0})
        stuck.depends_on = ["scheduled"]
        with pytest.raises(TimeoutError, match="timed out"):
            await stuck.execute(ctx)
        handle.status = "complete"
        done = AwaitBatchStep("wait", {"timeout_s": 5, "poll_s": 0.01})
        done.depends_on = ["scheduled"]
        out = await done.execute(ctx)
        assert out.data["batch"]["status"] == "complete" and len(out.data["runs"]) == 1
        with pytest.raises(ValueError, match="needs a batch_id"):
            await AwaitBatchStep("wait", {}).execute(_ctx(tmp_path))
    finally:
        reset_batches()
