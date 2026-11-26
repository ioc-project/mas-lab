#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared MCE metric computation logic.

Used by both ``mas-lab eval`` (library CLI) and ``eval_mce`` (pipeline step).

Session-level MCE metrics available
-------------------------------------
All metrics are LLM-as-judge (``mce_metrics_plugin``); they require a working
LLM service.  Call :func:`install_openai_llm_service` once per process before
computing; each trace then opens its own judge client (:func:`judge_client`).

Core session metrics:
- ``goal_success_rate``           — Did the agent accomplish the user's goal?
- ``groundedness``                — Is the response grounded in the retrieved context?
- ``response_completeness``       — Does the response address all aspects of the query?
- ``workflow_cohesion_index``     — How cohesive was multi-agent task coordination?
- ``workflow_efficiency``         — Was the workflow executed efficiently?
- ``consistency``                 — Were responses consistent across turns?
- ``context_preservation``        — Was conversational context preserved?
- ``information_retention``       — Was key information retained across turns?
- ``intent_recognition_accuracy`` — Was the user's intent correctly identified?
- ``component_conflict_rate``     — How often did component outputs conflict?

Note: ``task_delegation`` is NOT a session-level metric.
``TaskDelegationAccuracy`` exists in metrics_computation_engine as a span-level
stub (unfinished TODO prompt) and is not wired into this runner.
``answer_relevancy`` is now a session-level metric implemented in this runner.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from mas.library.eval.errors import JudgeCallFailed, judge_error_text
from mas.library.eval.mce.catalog import run_mce_class, session_scope
from mas.runtime import hostenv
from mas.runtime.engine.llm_output_limits import output_token_kwargs
from mas.runtime.secrets import SecretRef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DeepEval — native continuous scoring (no BinaryGrading constraint)
# ---------------------------------------------------------------------------

# Metrics in this set are computed via deepeval when binary_grading=False.
# deepeval scores are natively continuous (AnswerRelevancyMetric = fraction of
# relevant statements; GEval = weighted rubric 0-1 float).
_DEEPEVAL_METRIC_NAMES = {"answer_relevancy", "goal_success_rate"}


def _deepeval_llm_cls() -> Any:
    """DeepEval OpenAI-compatible LLM class (``GPTModel`` is a deprecated alias)."""
    try:
        from deepeval.models import OpenAIModel

        return OpenAIModel
    except ImportError:  # pragma: no cover - deepeval < 4.2
        from deepeval.models import GPTModel

        return GPTModel


def _make_deepeval_model(
    model: str,
    api_key: str,
    base_url: Optional[str],
) -> Any:
    """Build a ``deepeval.models.OpenAIModel`` from the resolved LLM config."""
    try:
        cls = _deepeval_llm_cls()
        return cls(
            model=model,
            api_key=api_key,
            base_url=base_url or None,
        )
    except Exception as exc:
        from mas.runtime.reliability.classify import error_summary

        logger.warning("deepeval OpenAIModel unavailable: %s", error_summary(exc))
        return None


def _compute_deepeval_score(
    metric_name: str,
    input_query: str,
    final_response: str,
) -> Dict[str, Any]:
    """Compute a single metric via deepeval and return a runner-compatible dict.

    Returns ``{"value": float|None, "reasoning": str, "error": str|None}``.
    Score is natively 0.0–1.0 continuous (no BinaryGrading schema injected).
    """
    if hostenv.get("MAS_MCE_OFFLINE", "").lower() in ("1", "true", "yes"):
        if not (input_query or "").strip():
            return {"value": None, "reasoning": "", "error": "input_query_empty"}
        if not (final_response or "").strip():
            return {"value": None, "reasoning": "", "error": "final_response_empty"}
        return {
            "value": 0.75,
            "reasoning": "offline stub (MAS_MCE_OFFLINE)",
            "error": None,
        }

    if _service is None:
        return {"value": None, "reasoning": "", "error": "deepeval model not initialised"}
    from mas.library.standard.lib.openai_client import openai_api_base, reveal_openai_key

    # Built for this score only: the key is revealed now and goes with the model when it returns.
    deepeval_model = _make_deepeval_model(
        model=_service.model,
        api_key=reveal_openai_key(_service.api_key, _service.base_url),
        base_url=openai_api_base(_service.base_url),
    )
    if deepeval_model is None:
        return {"value": None, "reasoning": "", "error": "deepeval model unavailable"}
    try:
        from deepeval.test_case import LLMTestCase

        test_case = LLMTestCase(input=input_query, actual_output=final_response)

        if metric_name == "answer_relevancy":
            from deepeval.metrics import AnswerRelevancyMetric

            metric = AnswerRelevancyMetric(
                model=deepeval_model,
                async_mode=False,
                verbose_mode=False,
            )
        elif metric_name == "goal_success_rate":
            from deepeval.metrics import GEval
            from deepeval.test_case import LLMTestCaseParams

            metric = GEval(
                name="GoalSuccessRate",
                criteria=(
                    "Does the response correctly correspond to what the user asked for? "
                    "Does it fulfill all expectations specified in the goal? "
                    "If the assistant cannot achieve the goal, does it state why?"
                ),
                evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
                model=deepeval_model,
                async_mode=False,
                verbose_mode=False,
            )
        else:
            return {"value": None, "reasoning": "", "error": f"no deepeval impl for {metric_name!r}"}

        metric.measure(test_case, _show_indicator=False)
        return {
            "value": float(metric.score),
            "reasoning": str(getattr(metric, "reason", "") or ""),
            "error": None,
        }
    except Exception as exc:
        summary = judge_error_text(exc)
        logger.error("deepeval metric %r failed: %s", metric_name, summary)
        return {"value": None, "reasoning": "", "error": summary}


# ---------------------------------------------------------------------------
# LLM service setup (openai SDK via public metrics_computation_engine)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _JudgeService:
    """What the installed judge calls. ``api_key`` is the SecretRef, never its value."""

    model: str
    base_url: str
    api_key: SecretRef
    output_kwargs: Dict[str, Any] = field(default_factory=dict)


_service: Optional[_JudgeService] = None
_jury: Any = None  # metrics_computation_engine.llm_judge.jury.Jury; carries no key
_run_client: ContextVar[Any] = ContextVar("mas_mce_judge_client", default=None)


def install_openai_llm_service(
    model_override: Optional[str] = None,
    *,
    api_key: object = None,
    model_source: Optional[str] = None,
) -> str:
    """Configure the judge (public MCE ``Jury``) on the openai SDK.

    Patches ``LLMClient.query`` at class level so that all ``Jury`` instances
    route through a judge client (no litellm required).  Idempotent.

    Only the model, base URL and key SecretRef are kept. The key is revealed when a
    judge run opens its client (:func:`judge_client`) and dropped when the run ends,
    so a rotated secret is picked up by the next run. The secret is checked here so
    that a missing key fails before the first trace.

    Configuration resolution order:
      1. *model_override* argument (eval_mce / experiment.evaluation.model /
         experiment.models.judge / experiment.model / models.main /
         application spec.models[])
      2. local ``config.yaml`` / package ``defaults.model`` (only when the
         spec said ``any``)
      3. Hard-coded fallback: ``gpt-4o``

    Returns the effective model id. Logs at INFO so the judge is visible
    next to summarizer model logs.
    """
    global _service, _jury
    from mas.library.standard.lib.openai_client import openai_key_ref, reveal_openai_key

    infra_api_base, infra_api_key, effective_model = _resolve_infra(model=model_override)
    base_url = str(infra_api_base or "")
    key_ref = openai_key_ref(api_key if api_key is not None else infra_api_key, base_url)
    service = _JudgeService(
        model=str(effective_model),
        base_url=base_url,
        api_key=key_ref,
        output_kwargs=output_token_kwargs(effective_model, generation=_resolve_infra_generation()),
    )
    reveal_openai_key(key_ref, base_url)
    if _jury is not None and service == _service:
        return service.model

    try:
        from mas.library.standard.lib.llm_scheduler import install_policy_from_pipeline

        proxy, _ws, leaves = _resolve_infra_proxy()
        install_policy_from_pipeline(_pipeline_for_routed_model(effective_model, proxy, leaves))
    except Exception as exc:
        logger.warning("llm_scheduler policy not installed: %s", exc)

    from metrics_computation_engine.llm_judge.jury import Jury
    from metrics_computation_engine.llm_judge.llm import LLMClient

    LLMClient.query = _jury_query  # type: ignore[method-assign]
    _service = service
    # The patched query opens its own judge client, so the jury is given no key.
    _jury = Jury({"LLM_MODEL_NAME": service.model, "LLM_BASE_MODEL_URL": service.base_url, "LLM_API_KEY": ""})

    source = model_source or ("override" if model_override else "defaults.model")
    logger.info(
        "MCE Jury configured (model=%s, source=%s, base_url=%s)",
        service.model,
        source,
        service.base_url or None,
    )
    return service.model


def _jury_query(self: Any, messages: list, **kwargs: Any) -> Any:
    """``LLMClient.query`` of the public MCE, on the judge client of the current run."""
    # Strip response_format — some proxies don't support JSON mode;
    # the prompt already instructs the model to return JSON.
    kwargs.pop("response_format", None)
    service = _require_service()
    model = self.model or service.model
    with judge_client() as client:

        def _once() -> Any:
            return paced_live_call(
                lambda: client.chat.completions.create(
                    model=model, messages=messages, **{**service.output_kwargs, **kwargs}
                ),
                model=model,
            )

        failure: JudgeCallFailed | None = None
        try:
            return _call_with_mce_retry(_once, model=model)
        except Exception as exc:
            failure = JudgeCallFailed(f"MCE LLM call failed (model={model!r}): {judge_error_text(exc)}")
        raise failure


def _require_service() -> _JudgeService:
    if _service is None:
        raise RuntimeError(
            "Judge LLM client is not installed. Call install_openai_llm_service() first (eval_mce does this)."
        )
    return _service


@contextmanager
def judge_client() -> Iterator[Any]:
    """The judge's OpenAI client for one run.

    The key is revealed on entry and the client is closed on exit; nothing outside the
    run keeps it. A nested use inside a run shares that run's client.
    """
    current = _run_client.get()
    if current is not None:
        yield current
        return
    service = _require_service()
    from mas.library.standard.lib.openai_client import openai_client_for_key

    client = openai_client_for_key(service.api_key, service.base_url)
    token = _run_client.set(client)
    try:
        yield client
    finally:
        _run_client.reset(token)
        client.close()


def get_effective_judge_model() -> str:
    """Return the model id last installed for the judge."""
    return _require_service().model


def get_jury() -> Any:
    """Return the MCE Jury installed by :func:`install_openai_llm_service`."""
    _require_service()
    return _jury


def llm_service_config() -> dict[str, str]:
    """Model and base URL last installed for the judge.

    The API key is not returned. Open :func:`judge_client` for a live request.
    """
    service = _require_service()
    return {
        "model": service.model,
        "base_url": service.base_url,
    }


def _pipeline_for_routed_model(
    model: Optional[str],
    proxy: Optional[dict[str, Any]],
    leaves: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Scheduler (and other per-leaf) steps for the leaf that owns *model*."""
    routed = proxy
    if leaves:
        from mas.runtime.registry.llm_provider_registry import leaf_for_model

        routed = leaf_for_model(model or "", leaves, default=proxy)
    steps = list((routed or {}).get("pipeline") or [])
    return steps or list((proxy or {}).get("pipeline") or [])


def _resolve_infra(*, model: Optional[str] = None) -> tuple[str, object, str]:
    """Resolve LLM proxy config from workspace infra refs.

    The judge model name selects the LLMProxy leaf (same routing as the agent).
    Returns ``(api_base, api_key SecretRef or None, model)``. ``None`` leaves the key to
    :func:`mas.library.standard.lib.openai_client.openai_key_ref`, which allows the
    default secret only for the default endpoint.
    """
    proxy, ws, leaves = _resolve_infra_proxy()
    if proxy is None:
        return ("", None, model or "gpt-4o")
    from mas.runtime.agent_defaults import resolve_default_model
    from mas.runtime.registry.llm_provider_registry import canonical_model_name, leaf_for_model

    leaf = proxy
    if leaves:
        leaf = leaf_for_model(model or "", leaves, default=proxy)
    if model:
        resolved_model = canonical_model_name(model, leaf)
    else:
        resolved_model = (
            resolve_default_model(ws) or leaf.get("default_model") or proxy.get("default_model") or "gpt-4o"
        )
    return (
        leaf.get("api_base", ""),
        leaf.get("api_key") or None,
        resolved_model,
    )


def _resolve_infra_generation() -> dict[str, Any]:
    """Infra ``spec.models.generation`` (output-token policy), empty when unresolved."""
    proxy, _ws, _leaves = _resolve_infra_proxy()
    return dict((proxy or {}).get("generation") or {})


def _resolve_infra_proxy() -> tuple[dict[str, Any] | None, Any, dict[str, dict[str, Any]]]:
    try:
        from mas.ctl.infra.resolve import resolve_infra_refs
        from mas.lab.workspace import WorkspaceConfig
    except ImportError:
        return None, None, {}

    ws = WorkspaceConfig.load()
    if not ws.found:
        return None, ws, {}

    refs = ws.effective_infra_refs
    if not refs:
        return None, ws, {}

    # An unresolved ref raises: an empty proxy would send the judge's key to the default endpoint.
    resolved = resolve_infra_refs(refs, workspace=ws)
    return resolved.llm_proxy, ws, dict(resolved.llm_proxies or {})


# ---------------------------------------------------------------------------
# Rate-limit-aware LLM call helper
# ---------------------------------------------------------------------------

_MCE_MAX_RETRIES = 4
_MCE_BASE_DELAY = 5.0  # seconds — doubles each retry, capped at 120 s


def _mce_retry_policy():
    from mas.library.standard.lib.retry import RetryPolicy
    from mas.runtime.reliability.classes import FailureClass

    return RetryPolicy(
        max_attempts=_MCE_MAX_RETRIES + 1,
        backoff_s=_MCE_BASE_DELAY,
        backoff_multiplier=2.0,
        jitter=True,
        retry_on=(FailureClass.TRANSIENT,),
        max_backoff_s=120.0,
    )


def _classify_mce_failure(exc: BaseException):
    """A judge failure as its type and status (:func:`judge_error_text`), never the SDK error's
    text: that text is the provider's body, which may echo the request. A 429 or an SDK ``RateLimitError`` is
    retried, and so is a 401/403 whose body says the proxy budget is spent (read, never copied)."""
    import httpx
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
    from mas.runtime.reliability.classify import (
        classify_http_exception,
        is_rate_limited,
        response_status,
        retry_after_seconds,
    )

    text = judge_error_text(exc)
    if is_rate_limited(exc):
        return ClassifiedFailure(
            text,
            failure_class=FailureClass.TRANSIENT,
            code="RATE_LIMIT",
            retry_after_s=retry_after_seconds(getattr(exc, "response", None)),
        )
    if response_status(exc) is not None:
        classified = classify_http_exception(exc, message=text)
        if classified.failure_class is FailureClass.TRANSIENT:
            return classified
    elif isinstance(exc, httpx.TransportError) or type(exc).__name__ in _SDK_TRANSPORT_ERRORS:
        return ClassifiedFailure(text, failure_class=FailureClass.TRANSIENT, code="TRANSPORT")
    return ClassifiedFailure(text, failure_class=FailureClass.APPLICATION, code="ERROR")


_SDK_TRANSPORT_ERRORS = frozenset({"APIConnectionError", "APITimeoutError"})


def _call_with_mce_retry(fn, *, model: str):
    from mas.library.standard.lib.retry import call_with_retry

    return call_with_retry(
        fn,
        policy=_mce_retry_policy(),
        classify=_classify_mce_failure,
        target=f"MCE LLM {model}",
        idempotent=True,
    )


def paced_live_call(fn, *, model: str):
    """Run a live judge HTTP call under the shared llm_scheduler slot."""
    from mas.library.standard.lib.llm_scheduler import llm_slot

    base_url = _service.base_url if _service is not None else ""
    provider = ""
    if base_url:
        try:
            provider = urlparse(base_url).hostname or ""
        except Exception:
            provider = base_url
    with llm_slot(model=model, provider=provider):
        return fn()


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_session_metrics(
    trace_path: Path,
    metric_names: List[str],
    response_agent_id: Optional[str] = None,
    *,
    binary_grading: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Compute MCE metrics for one trace file.

    Returns a dict mapping metric_id → ``{"value": float|None, "reasoning": str, "error": str|None}``.

    *response_agent_id* is forwarded to :class:`MASTraceProvider`.  When
    ``None`` (default), the root agent is auto-detected from the trace.

    *binary_grading* controls the scoring backend:

    * ``False`` (default) — metrics with a native deepeval implementation
      (``answer_relevancy``, ``goal_success_rate``) are computed via deepeval,
      which returns continuous 0.0–1.0 scores without any BinaryGrading schema.
      Other metrics fall back to MCE with BinaryGrading.
    * ``True`` — all metrics use MCE BinaryGrading (0 or 1 integer).
    """
    from mas.library.eval.mce.trace_provider import MASTraceProvider

    provider = MASTraceProvider(response_agent_id=response_agent_id)
    context = provider.fetch(str(trace_path), requirements=None)
    resource_id = _run_id_from_path(trace_path)
    # Inject session_id so MCE polymorphic metrics (Duration, Cost…) can
    # detect the resource type via _detect_resource_type().
    context["session_id"] = resource_id

    _input_query_missing = not context.get("input_query")
    _final_response_missing = not context.get("final_response")
    if _input_query_missing:
        logger.debug("%s — input_query empty", trace_path)
    if _final_response_missing:
        logger.debug("%s — final_response empty (response_agent=%r)", trace_path, response_agent_id or "<auto>")

    # Build a minimal SessionEntity from the context dict.
    # LLM-as-judge metrics need input_query, final_response, session_id, and
    # conversation_data for metrics that use conversation_data.get("conversation")
    # or conversation_data.get("elements") (e.g. WorkflowCohesionIndex,
    # ResponseCompleteness, Groundedness).
    from metrics_computation_engine.entities.models.session import SessionEntity

    conversation_text = context.get("conversation_text") or ""
    session = SessionEntity(
        session_id=resource_id,
        spans=[],  # required by pydantic; LLM-judge metrics only use input_query / final_response
        input_query=context.get("input_query") or "",
        final_response=context.get("final_response") or "",
        conversation_data={
            "conversation": conversation_text,
            "elements": conversation_text,
        }
        if conversation_text
        else None,
    )

    import asyncio

    # compute() is async in the public package; run synchronously in a dedicated loop
    # (safe because this function is called from a ThreadPoolExecutor thread).
    loop = asyncio.new_event_loop()

    results: Dict[str, Dict[str, Any]] = {}

    try:
        # One judge client (one reveal of the key) for the trace; closed when it is scored.
        with judge_client() if _service is not None else nullcontext():
            for name in metric_names:
                # Prefer deepeval for continuous-capable metrics when not in binary mode.
                if not binary_grading and name in _DEEPEVAL_METRIC_NAMES:
                    if not (context.get("input_query") or "").strip():
                        results[name] = {
                            "value": None,
                            "reasoning": "",
                            "error": "input_query_empty",
                        }
                        continue
                    if not (context.get("final_response") or "").strip():
                        results[name] = {
                            "value": None,
                            "reasoning": "",
                            "error": "final_response_empty",
                        }
                        continue
                    results[name] = _compute_deepeval_score(
                        name,
                        context.get("input_query") or "",
                        context.get("final_response") or "",
                    )
                    continue

                try:
                    outcome = loop.run_until_complete(run_mce_class(name, session, jury=_jury))
                except Exception as exc:
                    error = judge_error_text(exc)
                    logger.error("MCE metric %r failed on %s: %s", name, trace_path, error)
                    results[name] = {"value": None, "reasoning": "", "error": error}
                    continue
                if outcome is None:
                    results[name] = {"value": None, "reasoning": "", "error": f"import failed: {name}"}
                    continue
                results[name] = {
                    "value": float(outcome.value) if outcome.value is not None else None,
                    "reasoning": str(outcome.reasoning or ""),
                    "error": str(outcome.error_message) if outcome.error_message else None,
                }
    finally:
        loop.close()

    from mas.library.eval.metrics.scope import scope_details

    for name, score in results.items():
        unit, evidence = session_scope(name)
        details = dict(score.get("details") or {})
        details.update(scope_details(unit, evidence))
        score["details"] = details

    # Build run-quality metadata: warnings (data issues) + errors (metric failures)
    warnings: List[str] = []
    if _input_query_missing:
        warnings.append("input_query_empty")
    if _final_response_missing:
        warnings.append("final_response_empty")
    metric_errors = [name for name, v in results.items() if v.get("error")]
    results["__run_quality__"] = {
        "warnings": warnings,
        "errors": metric_errors,
        "status": "error" if metric_errors else ("warn" if warnings else "ok"),
    }

    return results


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

METRICS_SCHEMA_VERSION = "1"


def build_metrics_document(
    item_id: str,
    scenario: str,
    session_scores: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the canonical ``metrics.json`` document (schema v1)."""
    run_quality = session_scores.get("__run_quality__", {"warnings": [], "errors": [], "status": "ok"})
    session = {k: v for k, v in session_scores.items() if not k.startswith("__")}
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "item_id": item_id,
        "scenario": scenario,
        "session": session,
        "run_quality": run_quality,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _run_id_from_path(trace_path: Path) -> str:
    """Derive a human-readable run_id from the traces/events.jsonl path.

    E.g. ``baseline/item1/r1/traces/events.jsonl`` → ``baseline__item1__r1``.
    """
    parts = trace_path.parts
    # Find traces/ directory in path components
    try:
        idx = list(parts).index("traces")
        relevant = parts[max(0, idx - 3) : idx]
        return "__".join(relevant)
    except ValueError:
        return trace_path.parent.parent.name
