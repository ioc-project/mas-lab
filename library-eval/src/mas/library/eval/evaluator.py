#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic evaluation module — provider-agnostic metric computation.

Entry point for computing quality metrics from a knowledge graph.

Usage::

    from mas.library.eval.evaluator import evaluate_run, get_provider

    scores = evaluate_run(
        kg_path=Path("output/baseline/item1/r1/kg.json"),
        metrics=["goal_success_rate", "groundedness"],
        provider="mce",
    )
    # {"goal_success_rate": {"value": 0.85, "reasoning": "...", "error": None}}

Provider selection (explicit — no auto-fallback)::

    evaluate_run(kg_path, metrics, provider="mce")       # built-in MCE provider
    evaluate_run(kg_path, metrics, provider="mce_oss")   # metrics_computation_engine (OSS)
"""

from __future__ import annotations

import importlib
import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NotRequired, Optional, Sequence, TypedDict

if TYPE_CHECKING:
    from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs

logger = logging.getLogger(__name__)

DEFAULT_METRIC_PROVIDER = "mce"


class MetricScore(TypedDict):
    value: Optional[float]
    reasoning: str
    error: Optional[str]
    details: NotRequired[Optional[Dict[str, Any]]]


class EvalProvider:
    """Evaluation provider — stock metrics (subclass) plus registered :class:`EvalMetric`s.

        Custom metrics are registered on the existing provider (default ``mce``)
    with :meth:`register_metric`. :meth:`compute_metrics` is the single engine
    ``eval_mce`` uses.
    """

    name: str = DEFAULT_METRIC_PROVIDER

    def __init__(self) -> None:
        self._metrics: Dict[str, "EvalMetric"] = {}

    def compute(
        self,
        kg_path: Path,
        metric_names: List[str],
        *,
        response_agent_id: Optional[str] = None,
    ) -> Dict[str, MetricScore]:
        """Compute metrics for a run folder that contains ``kg.json``."""
        import asyncio

        from mas.library.eval.metrics import MetricContext

        del response_agent_id
        inputs = _run_inputs_from_kg(Path(kg_path))
        ctx = MetricContext(run_dir=inputs.run_dir)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.compute_metrics(metric_names, inputs, ctx))
        finally:
            loop.close()

    async def compute_metrics(
        self,
        metric_ids: Sequence[str],
        inputs: "RunInputs",
        ctx: "MetricContext",
        *,
        extra: Sequence["EvalMetric"] = (),
    ) -> Dict[str, MetricScore]:
        """Run stock, registered, and step-local metrics through one engine."""
        from mas.library.eval.metrics import normalize_score
        from mas.library.eval.metrics.plugins import load_declared_eval_metrics

        load_declared_eval_metrics()
        extras = {m.metric_id: m for m in extra}
        overlap = sorted(mid for mid in extras if self.resolve_metric(mid) is not None)
        if overlap:
            raise ValueError(f"prompt_metrics id(s) collide with stock MCE or a registered eval_metric: {overlap}")
        resolved: list[EvalMetric] = []
        unknown: list[str] = []
        listed = set(extras)
        for mid in metric_ids:
            if mid in listed:
                raise ValueError(f"Metric id {mid!r} is listed in both metrics and prompt_metrics")
            metric = self.resolve_metric(mid)
            if metric is None:
                unknown.append(mid)
            else:
                resolved.append(metric)
        if unknown:
            available = self.metric_ids()
            raise ValueError(
                f"Unknown eval metric id(s): {unknown}. Registered on provider {self.name!r}: {available or '(none)'}"
            )
        resolved.extend(extra)

        grouped: dict[str, list[EvalMetric]] = defaultdict(list)
        unbatched: list[EvalMetric] = []
        for metric in resolved:
            if metric.batch_key:
                grouped[metric.batch_key].append(metric)
            else:
                unbatched.append(metric)

        results: dict[str, MetricScore] = {}
        for metric in unbatched:
            try:
                score = await metric.compute(inputs, ctx)
            except Exception as exc:
                score = {"value": None, "reasoning": "", "error": str(exc)}
            results[metric.metric_id] = normalize_score(score)
        for _key, family in grouped.items():
            cls = type(family[0])
            try:
                batch = await cls.compute_batch(family, inputs, ctx)
            except Exception as exc:
                batch = {m.metric_id: {"value": None, "reasoning": "", "error": str(exc)} for m in family}
            for mid, score in batch.items():
                results[mid] = normalize_score(score)
        return results

    def available_metrics(self) -> List[str]:
        """Return metric IDs this provider supports."""
        return self.metric_ids()

    def resolve_metric(self, metric_id: str) -> "EvalMetric | None":
        """Return a hosted metric, or ``None`` if this provider does not know *metric_id*."""
        return self._hosted_metrics().get(metric_id)

    def register_metric(self, metric: "EvalMetric") -> None:
        """Register an :class:`EvalMetric` on this provider."""
        metric_id = str(getattr(metric, "metric_id", "") or "").strip()
        if not metric_id:
            raise ValueError("EvalMetric.metric_id must be a non-empty snake_case id")
        if self.resolve_metric(metric_id) is not None:
            raise ValueError(f"Duplicate eval metric id {metric_id!r} on provider {self.name!r}")
        for other_name in list_available_providers():
            if other_name == self.name:
                continue
            try:
                other = get_provider(other_name)
            except (ValueError, RuntimeError):
                continue
            if other.resolve_metric(metric_id) is not None:
                raise ValueError(f"Metric id {metric_id!r} is already registered on provider {other_name!r}")
        self._hosted_metrics()[metric_id] = metric

    def _hosted_metrics(self) -> Dict[str, "EvalMetric"]:
        hosted = getattr(self, "_metrics", None)
        if hosted is None:
            self._metrics = {}
            hosted = self._metrics
        return hosted

    @property
    def metrics(self) -> Dict[str, "EvalMetric"]:
        """id → registered EvalMetric (not stock catalog ids)."""
        return dict(self._hosted_metrics())

    def metric_ids(self) -> List[str]:
        """Return ids this provider can resolve (subclass may prepend stock ids)."""
        return list(self._hosted_metrics())


def _run_inputs_from_kg(kg_path: Path) -> "RunInputs":
    from mas.library.eval.metrics import RunInputs

    run_dir = kg_path.parent
    trace = run_dir / "traces" / "events.jsonl"
    return RunInputs(
        run_dir=run_dir,
        kg=kg_path,
        native_trace=trace if trace.exists() else None,
    )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_BUILTIN_PROVIDERS: Dict[str, str] = {
    "mce": "mas.library.eval.providers.mce",
    "mce_oss": "mas.library.eval.providers.mce_oss",
    "adversarial": "mas.library.eval.providers.adversarial",
}

_BUILTIN_CLASSES: Dict[str, str] = {
    "mce": "MCEProvider",
    "mce_oss": "MCEOSSProvider",
    "adversarial": "AdversarialProvider",
}

_PROVIDER_ALIASES: Dict[str, str] = {
    "mce_v1": "mce",
}

# Runtime registry — populated by register_provider().  Lab pipeline steps load
# domain-specific providers from lab-local files and register them here so that
# get_provider(name) works for the lifetime of the process without modifying
# the core evaluator module.
_RUNTIME_PROVIDERS: Dict[str, EvalProvider] = {}


def _canonical_provider_name(name: str) -> str:
    return _PROVIDER_ALIASES.get(name, name)


def register_provider(name: str, provider: EvalProvider) -> None:
    """Register a provider instance with the MCE wrapper at runtime.

    Called by pipeline steps that load lab-local provider implementations
    (e.g. ``eval_trip_planner_gt`` loading from ``eval/trip_planner_gt.py``).
    After registration, ``get_provider(name)`` returns this instance for the
    lifetime of the process.

    Args:
        name:     Provider identifier, e.g. ``"trip_planner_gt"``.
        provider: Fully-initialised EvalProvider instance.
    """
    _RUNTIME_PROVIDERS[_canonical_provider_name(name)] = provider


def list_available_providers() -> List[str]:
    """Return provider names that can be instantiated in this environment."""
    available = sorted(_RUNTIME_PROVIDERS.keys())
    for name, module_path in _BUILTIN_PROVIDERS.items():
        if name in _RUNTIME_PROVIDERS:
            continue
        try:
            importlib.import_module(module_path)
            available.append(name)
        except ImportError:
            continue
    aliased = [alias for alias, target in _PROVIDER_ALIASES.items() if target in available]
    return sorted(set(available) | set(aliased))


def get_provider(name: Optional[str] = None, **kwargs: Any) -> EvalProvider:
    """Instantiate an evaluation provider by name.

    Args:
        name: Provider id, e.g. ``"mce"``, ``"mce_oss"``, or ``"adversarial"``.
              Required — there is no ``"auto"`` fallback chain.
        **kwargs: Constructor arguments forwarded to built-in providers
              (e.g. ``dataset_path`` for ``"adversarial"``).

    Raises:
        ValueError: If *name* is missing or unknown.
        RuntimeError: If the provider's dependencies are not installed.
    """
    if not name or name == "auto":
        available = list_available_providers()
        raise ValueError(
            "Evaluation provider is required (no auto-fallback). "
            f"Available providers: {', '.join(available) or '(none — install mce or metrics_computation_engine)'}"
        )
    canonical = _canonical_provider_name(name)
    if canonical in _RUNTIME_PROVIDERS and not kwargs:
        return _RUNTIME_PROVIDERS[canonical]
    provider = _make_provider(canonical, **kwargs)
    if not kwargs:
        _RUNTIME_PROVIDERS[canonical] = provider
    return provider


def _make_provider(name: str, **kwargs: Any) -> EvalProvider:
    """Import and instantiate a provider.

    Raises:
        ValueError: Unknown provider name.
        RuntimeError: Dependencies missing (ImportError).
    """
    if name in _RUNTIME_PROVIDERS and not kwargs:
        return _RUNTIME_PROVIDERS[name]
    if name not in _BUILTIN_PROVIDERS:
        available = list_available_providers()
        raise ValueError(f"Unknown eval provider: {name!r}. Available providers: {', '.join(available) or '(none)'}")
    module_path = _BUILTIN_PROVIDERS[name]
    class_name = _BUILTIN_CLASSES[name]
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(
            f"Eval provider {name!r} is not available — missing dependencies. Install the package for {module_path}."
        ) from exc
    cls = getattr(mod, class_name)
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------


def evaluate_run(
    kg_path: Path,
    metrics: List[str],
    *,
    provider: Optional[str] = None,
    response_agent_id: Optional[str] = None,
) -> Dict[str, MetricScore]:
    """Compute metrics for a single run from its knowledge graph.

    Args:
        kg_path: Path to kg.json (output of normalize_events step).
        metrics: List of metric IDs to compute.
        provider: Provider id (``"mce"``, ``"mce_oss"``, etc.) — required.
        response_agent_id: Override root agent detection.

    Returns:
        Dict mapping metric_id → {value, reasoning, error}.
    """
    p = get_provider(provider)
    logger.info("evaluate_run: provider=%s, kg=%s, metrics=%s", p.name, kg_path, metrics)
    return p.compute(kg_path, metrics, response_agent_id=response_agent_id)


def list_metrics(provider: Optional[str] = None) -> List[str]:
    """Return available metric IDs from the selected provider."""
    return get_provider(provider).available_metrics()
