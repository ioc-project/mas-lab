#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control steps: model output is data, scores are not guessed, a pinned plan keeps the design."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.execution.wire import read_plan, select_runs
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import StepOutput
from mas.library.lab.steps.control._util import load_scores, safe_item_id, upstream_runs
from mas.library.lab.steps.control.inject_experiment import InjectExperimentStep
from mas.library.lab.steps.control.propose_prompts import _normalize_prompts


def _ctx(tmp_path: Path, config_path: Path | None = None) -> ExecutionContext:
    return ExecutionContext(
        pipeline=SimpleNamespace(config_path=config_path or tmp_path / "p.yaml", name="p"),
        output_dir=tmp_path,
        cache_manager=SimpleNamespace(),
    )


def test_prompt_count_is_capped_for_strings_too() -> None:
    assert len(_normalize_prompts({"prompts": ["a", "b", "c", "d"]}, n=1)) == 1
    assert len(_normalize_prompts({"prompts": [{"user_prompt": "a"}, "b", {"user_prompt": "c"}]}, n=2)) == 2


@pytest.mark.parametrize("bad", ["../../../tmp/x", "a/b", "..", "", " ", ".hidden", "x" * 200, None])
def test_model_chosen_ids_cannot_leave_the_run_folder(bad) -> None:
    out = _normalize_prompts({"prompts": [{"id": bad, "user_prompt": "hi"}]}, n=1)
    assert out[0]["id"] == "p1"
    assert safe_item_id("ok-1.2_x", "zz", set()) == "ok-1.2_x"


def test_duplicate_ids_are_made_unique() -> None:
    out = _normalize_prompts({"prompts": [{"id": "p", "user_prompt": "a"}, {"id": "p", "user_prompt": "b"}]}, n=2)
    assert len({row["id"] for row in out}) == 2


def test_oversize_and_empty_prompts_are_dropped() -> None:
    out = _normalize_prompts({"prompts": [{"user_prompt": "x" * 100000}, {"user_prompt": "  "}, "ok"]}, n=3)
    assert [row["user_prompt"] for row in out] == ["ok"]
    with pytest.raises(ValueError, match="no usable"):
        _normalize_prompts({"prompts": [""]}, n=1)


def test_table_scores_average_per_scenario_and_keep_ids_apart(tmp_path: Path) -> None:
    step = SimpleNamespace(config={}, depends_on=["t"], name="x")
    ctx = _ctx(tmp_path)
    ctx.step_outputs["t"] = StepOutput(
        data={
            "rows": [
                {"scenario": "A", "item_id": "A", "score": 1.0},
                {"scenario": "A", "item_id": "i2", "score": 0.0},
                {"scenario": "B", "item_id": "i2", "score": 0.5},
            ]
        }
    )
    scores = load_scores(step, ctx)
    assert scores.scenarios == {"A": 0.5, "B": 0.5}
    assert scores.items == {"A": 1.0, "i2": 0.25}


def test_upstream_runs_accepts_the_persisted_row_form(tmp_path: Path) -> None:
    step = SimpleNamespace(config={}, depends_on=["n"], name="x")
    ctx = _ctx(tmp_path)
    ctx.step_outputs["n"] = StepOutput(data={"run_rows": [{"scenario_id": "s", "item": {"id": 1}, "run_idx": 2}]})
    assert upstream_runs(ctx, step) == [PlannedRun("s", {"id": 1}, 2)]


def test_inject_after_halving_keeps_the_factorial_design_and_pins_through_a_plan(tmp_path: Path) -> None:
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "agent.yaml").write_text("kind: Agent\nmetadata: {name: d}\n", encoding="utf-8")
    (tpl / "overlay.yaml").write_text("x: 1\n", encoding="utf-8")
    exp = tpl / "experiment.yaml"
    exp.write_text(
        "experiment:\n  name: f\n  application:\n    manifest: ./agent.yaml\n  scenarios:\n"
        "    - id: a-x\n    - id: a-y\n    - id: b-x\n    - id: b-y\n"
        "  design:\n    plugin: factorial\n    params:\n      factors:\n        k: [a, b]\n        m: [x, y]\n"
        "  dataset:\n    path: ./d.yaml\n  run:\n    n_runs: 1\n",
        encoding="utf-8",
    )
    (tpl / "d.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d}\nspec:\n  items: []\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path, exp)
    ctx.step_outputs["prop"] = StepOutput(data={"prompts": [{"id": "n1", "user_prompt": "go"}]})
    ctx.step_outputs["halve"] = StepOutput(
        data={"runs": [PlannedRun("a-x", {"id": 1}, 0), PlannedRun("b-y", {"id": 1}, 0)]}
    )
    step = InjectExperimentStep(
        "inj",
        {
            "experiment": str(exp),
            "output": str(tmp_path / "next/experiment.yaml"),
            "dataset_output": str(tmp_path / "next/dataset.yaml"),
        },
    )
    step.depends_on = ["prop", "halve"]
    out = asyncio.run(step.execute(ctx))
    written = yaml.safe_load(Path(out.data["experiment_yaml"]).read_text(encoding="utf-8"))["experiment"]
    assert [s["id"] for s in written["scenarios"]] == ["a-x", "a-y", "b-x", "b-y"]  # design untouched
    assert Path(written["application"]["manifest"]).is_absolute()
    plan = read_plan(out.data["plan_file"])
    assert {row["scenario"] for row in plan["runs"]} == {"a-x", "b-y"}
    from mas.lab.benchmark.execution.planner import plan_experiment

    _, runs = plan_experiment(Path(out.data["experiment_yaml"]))
    chosen, _ = select_runs(runs, plan)
    assert [c.id for c in chosen] == [row["id"] for row in plan["runs"]]
