#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared MCE metric computation logic.

Used by both ``mas-lab eval`` (library CLI) and ``eval_mce`` (pipeline step).

Session-level MCE metrics available
-------------------------------------
All metrics are LLM-as-judge (``mce_metrics_plugin``); they require a working
LLM service.  Call :func:`install_openai_llm_service` once per process before
computing.

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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.library.eval.mce.catalog import run_mce_class, session_scope
from mas.runtime.engine.llm_output_limits import output_token_kwargs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DeepEval — native continuous scoring (no BinaryGrading constraint)
# ---------------------------------------------------------------------------

# Metrics in this set are computed via deepeval when binary_grading=False.
# deepeval scores are natively continuous (AnswerRelevancyMetric = fraction of
# relevant statements; GEval = weighted rubric 0-1 float).
_DEEPEVAL_METRIC_NAMES = {"answer_relevancy", "goal_success_rate"}

_deepeval_model: Any = None  # deepeval OpenAIModel instance; set by install_openai_llm_service


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
        logger.warning("deepeval OpenAIModel unavailable: %s", exc)
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
    if os.environ.get("MAS_MCE_OFFLINE", "").lower() in ("1", "true", "yes"):
        if not (input_query or "").strip():
            return {"value": None, "reasoning": "", "error": "input_query_empty"}
        if not (final_response or "").strip():
            return {"value": None, "reasoning": "", "error": "final_response_empty"}
        return {
            "value": 0.75,
            "reasoning": "offline stub (MAS_MCE_OFFLINE)",
            "error": None,
        }

    if _deepeval_model is None:
        return {"value": None, "reasoning": "", "error": "deepeval model not initialised"}
    try:
        from deepeval.test_case import LLMTestCase

        test_case = LLMTestCase(input=input_query, actual_output=final_response)

        if metric_name == "answer_relevancy":
            from deepeval.metrics import AnswerRelevancyMetric

            metric = AnswerRelevancyMetric(
                model=_deepeval_model,
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
                model=_deepeval_model,
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
        logger.error("deepeval metric %r failed: %s", metric_name, exc)
        return {"value": None, "reasoning": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# LLM service setup (openai SDK via public metrics_computation_engine)
# ---------------------------------------------------------------------------

_jury: Any = None  # metrics_computation_engine.llm_judge.jury.Jury
_jury_patched = False
_effective_model: str = ""
_openai_client: Any = None
_openai_base_url: str = ""
_openai_api_key: str = ""


def install_openai_llm_service(
    model_override: Optional[str] = None,
    *,
    api_key_env: Optional[str] = None,
    model_source: Optional[str] = None,
) -> str:
    """Create a ``Jury`` (public MCE) backed by the openai SDK.

    Patches ``LLMClient.query`` at class level so that all ``Jury`` instances
    route through our openai client (no litellm required).  Idempotent.

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
    global _jury, _jury_patched, _deepeval_model, _effective_model
    global _openai_client, _openai_base_url, _openai_api_key
    infra_api_base, infra_api_key_env, infra_model = _resolve_infra()
    effective_model = model_override or infra_model

    # Idempotency fast path: skip the reinstall only when the model this call
    # would actually use already matches the installed one — not merely
    # whether *this* call happened to pass an explicit override. A prior
    # call's override (or infra default) can differ from this call's, even
    # when both pass model_override=None.
    if _jury_patched and api_key_env is None and effective_model == _effective_model:
        return _effective_model

    effective_api_base = infra_api_base or None
    effective_api_key = os.environ.get(api_key_env or infra_api_key_env) or os.environ.get("OPENAI_API_KEY") or "none"

    from openai import OpenAI

    _client = OpenAI(api_key=effective_api_key, base_url=effective_api_base)
    _openai_client = _client
    _openai_base_url = str(effective_api_base or "")
    _openai_api_key = str(effective_api_key or "")
    _output_kwargs = output_token_kwargs(effective_model, generation=_resolve_infra_generation())

    from metrics_computation_engine.llm_judge.jury import Jury
    from metrics_computation_engine.llm_judge.llm import LLMClient

    def _query_patch(self: Any, messages: list, **kwargs: Any) -> Any:
        # Strip response_format — some proxies don't support JSON mode;
        # the prompt already instructs the model to return JSON.
        kwargs.pop("response_format", None)
        model = self.model or effective_model

        def _once() -> Any:
            return _client.chat.completions.create(model=model, messages=messages, **{**_output_kwargs, **kwargs})

        try:
            return _call_with_mce_retry(_once, model=model)
        except Exception as exc:
            raise RuntimeError(f"MCE LLM call failed (model={model!r}): {exc}") from exc

    LLMClient.query = _query_patch  # type: ignore[method-assign]

    llm_config = {
        "LLM_MODEL_NAME": effective_model,
        "LLM_BASE_MODEL_URL": effective_api_base or "",
        "LLM_API_KEY": effective_api_key,
    }
    _jury = Jury(llm_config)

    # Build deepeval model from the same config so continuous metrics use the same LLM.
    _deepeval_model = _make_deepeval_model(
        model=effective_model,
        api_key=effective_api_key,
        base_url=effective_api_base or None,
    )

    _jury_patched = True
    _effective_model = str(effective_model)
    source = model_source or ("override" if model_override else "defaults.model")
    logger.info(
        "MCE Jury configured (model=%s, source=%s, base_url=%s, deepeval=%s)",
        effective_model,
        source,
        effective_api_base,
        "ok" if _deepeval_model else "unavailable",
    )
    return effective_model


def get_openai_client() -> Any:
    """Return the OpenAI client installed by :func:`install_openai_llm_service`."""
    if _openai_client is None:
        raise RuntimeError(
            "Judge LLM client is not installed. Call install_openai_llm_service() first (eval_mce does this)."
        )
    return _openai_client


def get_effective_judge_model() -> str:
    """Return the model id last installed for the shared judge client."""
    if not _effective_model:
        raise RuntimeError(
            "Judge LLM client is not installed. Call install_openai_llm_service() first (eval_mce does this)."
        )
    return _effective_model


def get_jury() -> Any:
    """Return the MCE Jury installed by :func:`install_openai_llm_service`."""
    if _jury is None:
        raise RuntimeError(
            "Judge LLM client is not installed. Call install_openai_llm_service() first (eval_mce does this)."
        )
    return _jury


def llm_service_config() -> dict[str, str]:
    """Model, base URL, and API key last installed for the shared client.

    Callers (for example a Stateful Eval wrapper) must not copy these into
    process environment variables; pass them as constructor arguments.
    """
    if _openai_client is None:
        raise RuntimeError(
            "Judge LLM client is not installed. Call install_openai_llm_service() first (eval_mce does this)."
        )
    return {
        "model": _effective_model,
        "base_url": _openai_base_url,
        "api_key": _openai_api_key,
    }


def _resolve_infra() -> tuple[str, str, str]:
    """Resolve LLM proxy config from workspace ``config.yaml`` infra refs.

    Returns ``(api_base, api_key_env, model)``.
    """
    _FALLBACK = ("", "OPENAI_API_KEY", "gpt-4o")
    proxy, ws = _resolve_infra_proxy()
    if proxy is None:
        return _FALLBACK
    from mas.runtime.agent_defaults import resolve_default_model

    return (
        proxy.get("api_base", ""),
        proxy.get("api_key_env") or "OPENAI_API_KEY",
        resolve_default_model(ws) or proxy.get("default_model") or "gpt-4o",
    )


def _resolve_infra_generation() -> dict[str, Any]:
    """Infra ``spec.models.generation`` (output-token policy), empty when unresolved."""
    proxy, _ws = _resolve_infra_proxy()
    return dict((proxy or {}).get("generation") or {})


def _resolve_infra_proxy() -> tuple[dict[str, Any] | None, Any]:
    try:
        from mas.ctl.infra.resolve import resolve_infra_refs
        from mas.lab.workspace import WorkspaceConfig
    except ImportError:
        return None, None

    ws = WorkspaceConfig.load()
    if not ws.found:
        return None, ws

    refs = ws.effective_infra_refs
    if not refs:
        return None, ws

    try:
        resolved = resolve_infra_refs(refs, workspace=ws)
    except Exception as exc:
        logger.warning("Could not resolve infra refs %s: %s", refs, exc)
        return None, ws
    return resolved.llm_proxy, ws


# ---------------------------------------------------------------------------
# Rate-limit-aware LLM call helper
# ---------------------------------------------------------------------------

_MCE_MAX_RETRIES = 4
_MCE_BASE_DELAY = 5.0  # seconds — doubles each retry, capped at 120 s


def _is_ratelimit_error(exc: Exception) -> bool:
    """Return True if *exc* looks like a transient rate-limit / quota error.

    Checks for HTTP 429 or rate-limiting language in the error message.  HTTP
    401 alone is *not* treated as a rate-limit error — a plain auth failure
    (wrong API key) should fail immediately, not waste 4 retries.  The
    Outshift proxy signals budget exhaustion via a 401 whose message body
    contains "budget" or "quota", so those keywords still trigger a retry.
    """
    s = str(exc).lower()
    return any(tok in s for tok in ("429", "rate", "quota", "too many", "budget"))


def _mce_retry_policy():
    from mas.runtime.reliability.classes import FailureClass
    from mas.runtime.reliability.policy import RetryPolicy

    return RetryPolicy(
        max_attempts=_MCE_MAX_RETRIES + 1,
        backoff_s=_MCE_BASE_DELAY,
        backoff_multiplier=2.0,
        jitter=True,
        retry_on=(FailureClass.TRANSIENT,),
        max_backoff_s=120.0,
    )


def _classify_mce_failure(exc: BaseException):
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
    from mas.runtime.reliability.classify import classify_http_exception

    if _is_ratelimit_error(exc if isinstance(exc, Exception) else Exception(str(exc))):
        return ClassifiedFailure(str(exc), failure_class=FailureClass.TRANSIENT, code="RATE_LIMIT")
    classified = classify_http_exception(exc, message=str(exc))
    if classified.failure_class is FailureClass.TRANSIENT:
        return classified
    return ClassifiedFailure(str(exc), failure_class=FailureClass.APPLICATION, code="ERROR")


def _call_with_mce_retry(fn, *, model: str):
    from mas.runtime.reliability.retry import call_with_retry

    return call_with_retry(
        fn,
        policy=_mce_retry_policy(),
        classify=_classify_mce_failure,
        target=f"MCE LLM {model}",
        idempotent=True,
    )


def _llm_call_with_retry(client: Any, model: str, messages: list) -> str:
    """Call the OpenAI-compatible chat endpoint with exponential-backoff retry.

    Retries on rate-limit / transient errors up to ``_MCE_MAX_RETRIES`` times.
    Non-retryable errors are re-raised immediately as RuntimeError.
    """

    def _once() -> str:
        resp = client.chat.completions.create(model=model, messages=messages, **output_token_kwargs(model))
        return resp.choices[0].message.content or ""

    try:
        return _call_with_mce_retry(_once, model=model)
    except Exception as exc:
        raise RuntimeError(f"MCE LLM call failed (model={model!r}): {exc}") from exc


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
                logger.error("MCE metric %r failed on %s: %s", name, trace_path, exc)
                results[name] = {"value": None, "reasoning": "", "error": str(exc)}
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
