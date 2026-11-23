#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EvalMetric abstraction and run inputs."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from mas.library.eval.errors import judge_error_text
from mas.library.eval.evaluator import MetricScore

INPUT_KINDS = frozenset({"native_trace", "otel_spans", "kg"})


@dataclass
class RunInputs:
    """Lazy accessor over one run folder.

    Built by the pipeline step (not this library) so ``library-eval`` stays
    free of bench imports. Missing inputs stay ``None`` until :meth:`require`.
    """

    run_dir: Path | None = None
    native_trace: Path | None = None
    otel_spans: Path | None = None
    kg: Path | None = None
    run_info: dict[str, Any] | None = None

    def require(self, kind: str) -> Path:
        """Return the path for *kind*, or raise naming the missing input."""
        mapping = {
            "native_trace": self.native_trace,
            "otel_spans": self.otel_spans,
            "kg": self.kg,
        }
        if kind not in mapping:
            raise ValueError(f"Unknown metric input kind {kind!r}. Supported: {sorted(mapping)}")
        path = mapping[kind]
        if path is None:
            raise FileNotFoundError(
                f"Metric input {kind!r} is missing for run_dir={self.run_dir}. "
                "Produce that artefact (native events, otel_sdk_spans.jsonl, "
                "or kg.json) before requesting metrics that need it."
            )
        return path


@dataclass
class MetricContext:
    """Per-run context passed to :meth:`EvalMetric.compute`."""

    judge_model: str = ""
    judge_source: str = ""
    metric_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("mas.library.eval.metrics"))
    run_dir: Path | None = None
    response_agent_id: str | None = None
    quality_parts: list[dict[str, Any]] = field(default_factory=list)

    def options_for(self, metric_id: str) -> dict[str, Any]:
        return dict(self.metric_options.get(metric_id) or {})


class EvalMetric(ABC):
    """A metric shipped through MAS Lab.

    Shape mirrors MCE ``BaseMetric`` on purpose so it can be upstreamed later.

    ``unit`` and ``evidence`` are the metric's own contract: which I/O pair
    (``mas``, ``agent``, ``call``) and whether it needs only that pair
    (``io``) or the event history (``trajectory``). Stock MCE session ids
    are ``mas`` + ``io``. Default here is ``mas`` + ``trajectory``.
    """

    metric_id: str
    description: str = ""
    aggregation_level: Literal["session"] = "session"
    unit: Literal["mas", "agent", "call"] = "mas"
    evidence: Literal["io", "trajectory"] = "trajectory"
    input_kinds: frozenset[str] = frozenset({"native_trace"})
    requires_llm: bool = True
    batch_key: str | None = None

    @abstractmethod
    async def compute(self, inputs: RunInputs, ctx: MetricContext) -> MetricScore:
        """Compute this metric for one run."""

    @classmethod
    async def compute_batch(
        cls,
        metrics: Sequence["EvalMetric"],
        inputs: RunInputs,
        ctx: MetricContext,
    ) -> dict[str, MetricScore]:
        """Compute a family of metrics. Default: loop :meth:`compute`."""
        results: dict[str, MetricScore] = {}
        for metric in metrics:
            try:
                results[metric.metric_id] = await metric.compute(inputs, ctx)
            except Exception as exc:
                results[metric.metric_id] = {
                    "value": None,
                    "reasoning": "",
                    "error": judge_error_text(exc),
                }
        return results


def normalize_score(score: MetricScore | dict[str, Any]) -> MetricScore:
    """Ensure a score dict has the MetricScore keys."""
    details = score.get("details")
    out: MetricScore = {
        "value": score.get("value"),
        "reasoning": str(score.get("reasoning") or ""),
        "error": score.get("error"),
    }
    if details is not None:
        out["details"] = details
    return out
