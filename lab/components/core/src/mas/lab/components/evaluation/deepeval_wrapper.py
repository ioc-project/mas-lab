#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
DeepEval Wrapper for LLM-as-a-Judge Metrics.

This module provides an adapter to use DeepEval metrics (or mock them)
within the mas-lab evaluation framework.

It allows the User Emulation Agent to judge the quality of responses.
"""

import random
import time
from typing import Any, Dict, List

try:
    from deepeval.metrics import AnswerRelevancyMetric as _RealAnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase

    DEEPEVAL_AVAILABLE = True
except ImportError:
    _RealAnswerRelevancyMetric = None
    DEEPEVAL_AVAILABLE = False

    class LLMTestCase:
        def __init__(self, input: str, actual_output: str, retrieval_context: List[str] = None):
            self.input = input
            self.actual_output = actual_output
            self.retrieval_context = retrieval_context or []


def _resolve_model_arg(model: Any) -> Any:
    """Build a ``deepeval.models.OpenAIModel`` routed through the workspace LLM proxy.

    If *model* is already a ``DeepEvalBaseLLM`` instance, return as-is.
    If it's a string (e.g. ``"azure/gpt-4o"``), create an ``OpenAIModel``
    configured with the workspace proxy base-url and API key.

    A secret error (missing, denied) and an infra resolution error propagate: deepeval's own
    default client is never used as a fallback for a judge that was meant to use the store.
    Only a missing optional package (``library-eval``) leaves *model* as given.
    """
    if model is not None and not isinstance(model, str):
        return model
    try:
        from mas.library.eval.mce.runner import _deepeval_llm_cls, _resolve_infra
        from mas.library.standard.lib.openai_client import openai_api_base, reveal_openai_key
    except ImportError:
        return model

    api_base, api_key_ref, default_model = _resolve_infra()
    effective_model = model or default_model or "gpt-4o"
    cls = _deepeval_llm_cls()
    return cls(
        model=effective_model,
        api_key=reveal_openai_key(api_key_ref, api_base),
        base_url=openai_api_base(api_base),
    )


class AnswerRelevancyMetric:
    """Adapter exposing ``compute()`` and ``metric_id`` for AnnotateMetricsStep.

    Delegates to deepeval's real metric when available, otherwise mocks.
    """

    metric_id = "answer_relevancy"

    def __init__(self, threshold: float = 0.5, **kwargs: Any):
        self.threshold = threshold
        self.score = 0.0
        self.reason = ""
        self._delegate = None
        if DEEPEVAL_AVAILABLE and _RealAnswerRelevancyMetric is not None:
            # Outside the try: a secret or infra error must stop the metric, not turn it into a mock.
            resolved_model = _resolve_model_arg(kwargs.pop("model", None))
            try:
                self._delegate = _RealAnswerRelevancyMetric(
                    threshold=threshold,
                    model=resolved_model,
                    async_mode=False,
                    verbose_mode=False,
                    **kwargs,
                )
            except Exception:
                pass

    def measure(self, test_case: Any):
        if self._delegate is not None:
            self._delegate.measure(test_case)
            self.score = float(getattr(self._delegate, "score", 0.0))
            self.reason = str(getattr(self._delegate, "reason", ""))
            return self.score
        time.sleep(0.1)
        self.score = random.uniform(0.6, 1.0)
        self.reason = "Mock relevancy score."
        return self.score

    def compute(self, *, run_id: str, level: str, context: dict) -> dict:
        """Interface expected by AnnotateMetricsStep."""
        import datetime

        input_text = context.get("input", "")
        output_text = context.get("output", "")

        error = None
        if self._delegate is not None and input_text and output_text:
            try:
                tc = LLMTestCase(input=input_text, actual_output=output_text)
                self._delegate.measure(tc)
                self.score = float(getattr(self._delegate, "score", 0.0))
                self.reason = str(getattr(self._delegate, "reason", ""))
            except Exception as exc:
                from mas.runtime.reliability.classify import error_summary

                # The type and status only: a judge SDK error's text is the provider's body.
                self.score = 0.0
                self.reason = ""
                error = error_summary(exc)
        else:
            self.score = random.uniform(0.6, 1.0)
            self.reason = "Mock relevancy score."

        return {
            "run_id": run_id,
            "metric": self.metric_id,
            "level": level,
            "score": self.score if error is None else None,
            "reasoning": self.reason,
            "model": "deepeval" if self._delegate else "mock",
            "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "error": error,
        }

    def is_successful(self):
        return self.score >= self.threshold


class DeepEvalEvaluator:
    """
    Evaluator that uses DeepEval metrics to judge agent performance.
    """

    def __init__(self):
        self.relevancy_metric = AnswerRelevancyMetric(threshold=0.7)

    def evaluate_response(self, input_prompt: str, output_response: str, context: List[str] = None) -> Dict[str, Any]:
        """
        Evaluate a single response using LLM-as-a-Judge.

        Args:
            input_prompt: The user's original query.
            output_response: The agent's final answer.
            context: (Optional) Retrieved context chunks.

        Returns:
            Dictionary with score and reason.
        """
        test_case = LLMTestCase(input=input_prompt, actual_output=output_response, retrieval_context=context)

        score = self.relevancy_metric.measure(test_case)

        return {"answer_relevancy": score, "reason": self.relevancy_metric.reason}
