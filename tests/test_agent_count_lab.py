#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""labs/agent-count.lab: each scenario sets the group size, and per-run metrics score the answers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mas.ctl.compile import compile_manifest

LAB = Path(__file__).resolve().parents[1] / "labs" / "agent-count.lab"
SIZES = (1, 2, 4, 8, 16)


@pytest.fixture(scope="module")
def metrics():
    sys.path.insert(0, str(LAB))
    try:
        from agent_count_lab import metrics

        yield metrics
    finally:
        sys.path.remove(str(LAB))


@pytest.mark.parametrize("size", SIZES)
def test_scenario_overlay_sets_the_group_size(size: int) -> None:
    compiled = compile_manifest(
        LAB / "apps/crowd-qa/mas.yaml", [LAB / f"overlays/n{size}.yaml"], fill_defaults=False, validate=True
    )
    workers = [f"worker-{i}" for i in range(1, size + 1)]
    assert compiled.agent_ids == ["moderator", *workers]
    nodes = {node["id"]: node for node in compiled.mas["spec"]["workflow"]["nodes"]}
    assert nodes["moderator"]["delegates_to"] == workers
    assert set(nodes) == {"moderator", *workers}
    assert compiled.mas["spec"]["agency"]["groups"] == {"worker": workers}


def test_normalize_takes_the_last_integer_or_the_text(metrics) -> None:
    assert metrics.normalize("The answer is 1,368.") == "1368"
    assert metrics.normalize(" 408 ") == "408"
    assert metrics.normalize("Seven") == "seven"
    assert metrics.normalize(None) == ""


def test_answers_keep_the_last_complete_output_of_each_agent_that_called_the_model(metrics) -> None:
    events = [
        {"kind": "llm_call_end", "agent_id": "worker-1", "output": "3", "finish_reason": "stop"},
        {"kind": "llm_call_end", "agent_id": "worker-2", "output": "2", "finish_reason": "stop"},
        {"kind": "llm_call_end", "agent_id": "worker-2", "output": "3", "finish_reason": "stop"},
        {"kind": "llm_call_end", "agent_id": "worker-3", "output": "cut off 9", "finish_reason": "length"},
        {
            "kind": "llm_call_end",
            "agent_id": "worker-4",
            "output": "LLM request failed: HTTP 403.",
            "finish_reason": None,
        },
        {"kind": "tool_call_end", "agent_id": "moderator", "output": "x"},
    ]
    assert metrics.answers_of(events) == {"worker-1": "3", "worker-2": "3"}


def test_score_agreement_accuracy_and_strict_majority(metrics) -> None:
    assert metrics.score({"a": "3", "b": "3", "c": "2"}, "3") == {
        "agreement": pytest.approx(2 / 3),
        "accuracy": pytest.approx(2 / 3),
        "majority_correct": 1.0,
    }
    tie = metrics.score({"a": "3", "b": "2"}, "3")
    assert tie["majority_correct"] == 0 and tie["agreement"] == 0.5
    wrong = metrics.score({"a": "2", "b": "2", "c": "3"}, "3")
    assert wrong["majority_correct"] == 0 and wrong["accuracy"] == pytest.approx(1 / 3)
    assert metrics.score({"worker-1": "5"}, "7")["agreement"] == 1.0


def test_dataset_ground_truth_is_one_integer_per_item(metrics) -> None:
    gold = metrics.gold_answers(str(LAB / "datasets/crowd-questions.yaml"))
    assert len(gold) == 10
    assert all(answer.lstrip("-").isdigit() for answer in gold.values())


def _run(tmp_path: Path, item: str, calls: list[tuple[str, str, str | None, int]]) -> Path:
    """A run folder ``n4/item<item>/r1`` whose trace holds the given model calls (agent, output, finish, tokens)."""
    run_dir = tmp_path / "n4" / f"item{item}" / "r1"
    (run_dir / "traces").mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "kind": "llm_call_end",
                "agent_id": agent,
                "output": out,
                "finish_reason": finish,
                "tokens_used": {"total_tokens": tokens},
            }
        )
        for agent, out, finish, tokens in calls
    ]
    (run_dir / "traces" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


async def _scores(metrics, run_dir: Path) -> dict:
    from mas.library.eval.metrics import MetricContext, RunInputs

    inputs = RunInputs(run_dir=run_dir, native_trace=run_dir / "traces" / "events.jsonl")
    ctx = MetricContext(run_dir=run_dir)
    return {m.metric_id: await m.compute(inputs, ctx) for m in metrics.build_metrics()}


async def test_a_run_is_scored_by_every_metric(metrics, tmp_path) -> None:
    gold = metrics.gold_answers(str(LAB / "datasets/crowd-questions.yaml"))
    item = next(iter(gold))
    right, wrong = gold[item], "-1"
    run_dir = _run(
        tmp_path,
        item,
        [
            ("worker-1", f"Answer: {right}", "stop", 100),
            ("worker-2", f"Answer: {right}", "stop", 100),
            ("worker-3", f"Answer: {wrong}", "stop", 100),
            ("worker-4", "cut off", "length", 100),
            ("worker-5", "LLM request failed: HTTP 403.", None, 0),
        ],
    )
    out = await _scores(metrics, run_dir)
    assert out["crowd_agents"]["value"] == 3
    assert out["crowd_agents"]["details"] == {"n_truncated": 1, "n_failed": 1}
    assert out["crowd_agreement"]["value"] == pytest.approx(2 / 3)
    assert out["crowd_accuracy"]["value"] == pytest.approx(2 / 3)
    assert out["crowd_majority_correct"]["value"] == 1.0
    assert out["crowd_tokens"]["value"] == 400
    assert all(score["error"] is None for score in out.values())


async def test_a_run_with_no_complete_answer_scores_null_with_an_error(metrics, tmp_path) -> None:
    item = next(iter(metrics.gold_answers(str(LAB / "datasets/crowd-questions.yaml"))))
    out = await _scores(metrics, _run(tmp_path, item, [("worker-1", "cut off", "length", 5)]))
    assert out["crowd_agreement"]["value"] is None and out["crowd_agreement"]["error"]
    assert out["crowd_agents"]["value"] == 0


async def test_an_item_missing_from_the_dataset_is_an_error_not_a_zero(metrics, tmp_path) -> None:
    out = await _scores(metrics, _run(tmp_path, "nope", [("worker-1", "Answer: 1", "stop", 5)]))
    assert out["crowd_accuracy"]["value"] is None and "nope" in out["crowd_accuracy"]["error"]


def test_the_pipeline_uses_registered_metrics_and_the_standard_steps(metrics) -> None:
    import yaml

    doc = yaml.safe_load((LAB / "experiment.yaml").read_text(encoding="utf-8"))["experiment"]
    (judge,) = doc["run"]["post"]
    assert judge["type"] == "eval_mce"
    assert set(judge["config"]["metrics"]) == {m.metric_id for m in metrics.build_metrics()}
    assert {step["type"] for step in doc["post"]} == {"collect_metrics", "compute_ci", "plotnine"}
    assert doc["run"]["n_runs"] >= 10
