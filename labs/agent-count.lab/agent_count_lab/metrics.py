#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Per-run metrics of agent-count.lab, read from ``events.jsonl`` (no judge model, no LLM call).

They are ``eval_metric`` plugins: the lab lists their ids under ``eval_mce`` and the standard steps
(``collect_metrics``, ``compute_ci``, ``plotnine``) aggregate them over the runs.

Each run's answers are the last complete output of every agent id that called the model (the moderator
never does, so the answers are the workers'; no agent name is read). A call is an answer only when it
ended with ``finish_reason: stop``; a call cut off by the token limit or ended by an endpoint error is not.
An answer is the last integer in the output, or the lower-cased text when there is none.

``crowd_agents``            agents that answered (below the scenario's size: a worker was cut off or failed;
                            ``details`` has ``n_truncated`` and ``n_failed``)
``crowd_agreement``         share of agents that gave the most common answer
``crowd_accuracy``          share of agents that gave the dataset's ``ground_truth``
``crowd_majority_correct``  1 when the ground truth is the strict plurality, else 0 (a tie does not count)
``crowd_tokens``            tokens of all model calls in the run

The ground truth comes from the item (the run folder ``item<id>``) in the dataset given by the metric option
``dataset`` (default: the lab's own).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from mas.library.eval.evaluator import MetricScore
from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs

_LAB_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _LAB_ROOT / "datasets" / "crowd-questions.yaml"
_NUMBER = re.compile(r"-?\d+")


def normalize(text: Any) -> str:
    """The comparable form of one answer."""
    text = str(text or "").strip().lower().replace(",", "")
    numbers = _NUMBER.findall(text)
    return numbers[-1] if numbers else text.strip(" .!\"'")


def read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if isinstance(record, dict):
                events.append(record)
    return events


def _calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "llm_call_end"]


def answers_of(events: list[dict[str, Any]]) -> dict[str, str]:
    """Last complete model output per agent id: only a call that ended with ``stop`` is an answer."""
    return {
        str(e["agent_id"]): normalize(e.get("output"))
        for e in _calls(events)
        if e.get("agent_id") and e.get("finish_reason") == "stop"
    }


def score(answers: dict[str, str], gold: str) -> dict[str, float]:
    """Agreement, accuracy and majority correctness of *answers* (agent id to answer) against *gold*."""
    counts = Counter(answers.values())
    n = len(answers)
    gold_n = counts.get(gold, 0)
    others = max((c for a, c in counts.items() if a != gold), default=0)
    return {
        "agreement": counts.most_common(1)[0][1] / n,
        "accuracy": gold_n / n,
        "majority_correct": float(gold_n > others),
    }


@lru_cache(maxsize=8)
def gold_answers(dataset: str) -> dict[str, str]:
    """Item id to expected answer."""
    doc = yaml.safe_load(Path(dataset).read_text(encoding="utf-8"))
    return {
        str(item["id"]): normalize((item.get("expectations") or {}).get("ground_truth"))
        for item in doc["spec"]["items"]
    }


class _CrowdMetric(EvalMetric):
    unit = "mas"
    evidence = "trajectory"
    requires_llm = False
    batch_key = "crowd"
    field = ""

    async def compute(self, inputs: RunInputs, ctx: MetricContext) -> MetricScore:
        events = read_events(inputs.require("native_trace"))
        answers = answers_of(events)
        calls = _calls(events)
        details = {
            "n_truncated": sum(1 for e in calls if e.get("finish_reason") == "length"),
            "n_failed": sum(1 for e in calls if not e.get("finish_reason")),
        }
        if self.field == "agents":
            return {
                "value": float(len(answers)),
                "reasoning": "agents with a complete answer",
                "error": None,
                "details": details,
            }
        if self.field == "tokens":
            total = sum(int((e.get("tokens_used") or {}).get("total_tokens") or 0) for e in calls)
            return {"value": float(total), "reasoning": "tokens of all model calls", "error": None}
        if not answers:
            return {"value": None, "reasoning": "", "error": "no agent gave a complete answer", "details": details}
        run_dir = ctx.run_dir
        item = run_dir.parent.name.removeprefix("item") if run_dir else ""
        dataset = str(ctx.options_for(self.metric_id).get("dataset") or _DEFAULT_DATASET)
        gold = gold_answers(dataset).get(item)
        if gold is None:
            return {"value": None, "reasoning": "", "error": f"item {item!r} is not in {dataset}"}
        value = score(answers, gold)[self.field]
        return {"value": value, "reasoning": f"{len(answers)} answers, expected {gold}", "error": None}


def _metric(metric_id: str, field: str, description: str) -> _CrowdMetric:
    metric = _CrowdMetric()
    metric.metric_id = metric_id
    metric.field = field
    metric.description = description
    return metric


def build_metrics() -> list[EvalMetric]:
    return [
        _metric("crowd_agents", "agents", "Agents that gave a complete answer."),
        _metric("crowd_agreement", "agreement", "Share of agents on the most common answer."),
        _metric("crowd_accuracy", "accuracy", "Share of agents that gave the ground truth."),
        _metric("crowd_majority_correct", "majority_correct", "1 when the ground truth is the strict plurality."),
        _metric("crowd_tokens", "tokens", "Tokens of all model calls in the run."),
    ]
