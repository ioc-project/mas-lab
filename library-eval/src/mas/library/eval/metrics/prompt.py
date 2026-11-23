#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Step-local LLM-as-judge metrics: an id, a prompt, and an explicit evidence slice."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from mas.library.eval.errors import judge_error_text
from mas.library.eval.evaluator import MetricScore
from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs
from mas.library.eval.metrics.llm_json import complete_json
from mas.library.eval.metrics.scope import (
    parse_evidence,
    parse_unit,
    render_judge_input,
    scope_details,
)

METRIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PromptJudgeMetric(EvalMetric):
    """Inline metric: YAML is the implementation.

    *unit* and *evidence* are part of that definition (what the metric
    needs), not step-level switches. *prompt* (and optional *system*) are
    the full judge instruction.
    """

    aggregation_level = "session"
    input_kinds = frozenset({"native_trace"})
    requires_llm = True
    batch_key = "prompt_judge_v1"

    def __init__(
        self,
        metric_id: str,
        prompt: str,
        *,
        unit: str,
        evidence: str,
        system: str | None = None,
        agent_id: str | None = None,
        description: str | None = None,
    ) -> None:
        self.metric_id = _require_id(metric_id)
        self.prompt = str(prompt or "").strip()
        if not self.prompt:
            raise ValueError(f"prompt_metrics {self.metric_id!r} needs a non-empty prompt")
        self.unit = parse_unit(unit)
        self.evidence = parse_evidence(evidence)
        agent = str(agent_id or "").strip()
        self.agent_id = agent or None
        if self.unit == "agent" and not self.agent_id:
            raise ValueError(f"prompt_metrics {self.metric_id!r}: unit=agent requires agent")
        system_text = str(system or "").strip()
        self.system = system_text or None
        written = str(description or "").strip()
        self.description = written or (f"Inline metric {self.metric_id} (unit={self.unit}, evidence={self.evidence}).")

    async def compute(self, inputs: RunInputs, ctx: MetricContext) -> MetricScore:
        batch = await self.compute_batch([self], inputs, ctx)
        return batch[self.metric_id]

    @classmethod
    async def compute_batch(
        cls,
        metrics: Sequence[EvalMetric],
        inputs: RunInputs,
        ctx: MetricContext,
    ) -> dict[str, MetricScore]:
        try:
            events_path = inputs.require("native_trace")
        except FileNotFoundError as exc:
            err = str(exc)
            return {m.metric_id: {"value": None, "reasoning": "", "error": err} for m in metrics}
        out: dict[str, MetricScore] = {}
        for metric in metrics:
            if not isinstance(metric, PromptJudgeMetric):
                continue
            out[metric.metric_id] = _score_one(metric, events_path)
        return out


def parse_prompt_metrics(raw: Any) -> list[PromptJudgeMetric]:
    """Parse ``eval_mce.config.prompt_metrics`` into :class:`PromptJudgeMetric`."""
    if raw in (None, [], ()):
        return []
    if not isinstance(raw, list):
        raise ValueError("prompt_metrics must be a list of {id, prompt, unit, evidence} objects")
    seen: set[str] = set()
    metrics: list[PromptJudgeMetric] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"prompt_metrics[{index}] must be an object with id, prompt, unit, evidence")
        metric_id = str(item.get("id") or item.get("metric_id") or "").strip()
        prompt = str(item.get("prompt") or item.get("question") or "").strip()
        if metric_id in seen:
            raise ValueError(f"duplicate prompt_metrics id {metric_id!r}")
        seen.add(metric_id)
        if "unit" not in item or "evidence" not in item:
            raise ValueError(
                f"prompt_metrics[{index}] ({metric_id or 'missing id'}) is an "
                "inline metric and must set unit (mas|agent|call) and "
                "evidence (io|trajectory)"
            )
        metrics.append(
            PromptJudgeMetric(
                metric_id,
                prompt,
                unit=item.get("unit"),
                evidence=item.get("evidence"),
                system=item.get("system"),
                agent_id=item.get("agent") or item.get("agent_id"),
                description=item.get("description"),
            )
        )
    return metrics


def _require_id(metric_id: str) -> str:
    token = str(metric_id or "").strip()
    if not METRIC_ID_RE.fullmatch(token):
        raise ValueError(
            f"prompt_metrics id {metric_id!r} must be snake_case "
            r"([a-z][a-z0-9_]*)"
        )
    return token


def _score_one(metric: PromptJudgeMetric, events_path: Any) -> MetricScore:
    base_details = scope_details(
        metric.unit,
        metric.evidence,
        prompt=metric.prompt,
        agent_id=metric.agent_id,
    )
    try:
        slice_text, meta = render_judge_input(
            events_path,
            unit=metric.unit,
            evidence=metric.evidence,
            agent_id=metric.agent_id,
        )
    except Exception as exc:
        return {
            "value": None,
            "reasoning": "",
            "error": str(exc),
            "details": base_details,
        }
    user = f"{metric.prompt}\n\n---\n\n{slice_text}"
    messages: list[dict[str, str]] = []
    if metric.system:
        messages.append({"role": "system", "content": metric.system})
    messages.append({"role": "user", "content": user})
    details = {**base_details, **meta}
    try:
        payload = complete_json(
            messages,
            temperature=0.0,
            retries=2,
        )
        value, reasoning = value_from_judge(payload)
    except Exception as exc:
        details["truncated"] = meta.get("truncated")
        return {
            "value": None,
            "reasoning": "",
            "error": judge_error_text(exc),
            "details": details,
        }
    details["judge"] = payload
    return {
        "value": value,
        "reasoning": reasoning,
        "error": None,
        "details": details,
    }


def value_from_judge(payload: dict[str, Any]) -> tuple[float, str]:
    """Read ``value`` or YES/NO ``answer`` from a judge JSON object."""
    reasoning = str(payload.get("reasoning") or payload.get("evidence") or payload.get("why") or "").strip()
    if payload.get("value") is not None and str(payload.get("value")).strip() != "":
        try:
            number = float(payload["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"judge value is not a number: {payload.get('value')!r}") from exc
        return number, reasoning
    answer = str(payload.get("answer") or payload.get("verdict") or payload.get("ANSWER") or "").strip().lower()
    token = answer.split()[0] if answer else ""
    if token in {"yes", "y", "true", "pass", "1"}:
        return 1.0, reasoning or answer
    if token in {"no", "n", "false", "fail", "0"}:
        return 0.0, reasoning or answer
    raise ValueError(f"judge JSON must include numeric 'value' or answer YES/NO; got keys {sorted(payload)}")
