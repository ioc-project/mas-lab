#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TripPlannerGTProvider — ground-truth evaluation for MAS necessity items.

Demonstrates how to extend MCE with a domain-specific provider.  Standard
MCE metrics (goal_success_rate) judge answers without a reference — they rate
a confident hallucination as "successful".  This provider uses Copilot-generated
ground truths embedded in the dataset to ask the judge "is THIS specific fact
correct?" rather than "does this answer sound reasonable?".

Metrics
-------
- ``key_facts_accuracy``  — LLMaaJ checking every numeric/factual key in
  ``item.ground_truth`` against the agent response (mn1–mn4).
- ``claim_verification``  — Binary: did the agent correctly identify whether
  the blogger's claim is right or wrong, and name the violated constraint (mn3)?

These metrics complement AdversarialProvider (mn10–mn12) and standard MCE
(goal_success_rate, groundedness) to produce a complete picture of where each
topology succeeds and where it hallucinates.

Usage::

    from mas.library.eval import get_provider

    provider = get_provider("trip_planner_gt",
                            dataset_path=Path("datasets/mas-necessity.yaml"),
                            llm_model="azure/gpt-4o-mini",
                            api_key_env="OPENAI_API_KEY",
                            api_base="https://api.openai.com/v1")
    scores = provider.compute(
        kg_path=Path("output/mn1/r1/kg.json"),
        metric_names=["key_facts_accuracy"],
    )
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from mas.library.eval.evaluator import EvalProvider, MetricScore
from mas.runtime.engine.llm_output_limits import output_token_kwargs
from mas.runtime.spec.source import load_dataset_items_file as _load_dataset_file

logger = logging.getLogger(__name__)


class TripPlannerGTProvider(EvalProvider):
    """Ground-truth evaluation provider for MAS necessity items (mn1–mn4).

    Uses LLM-as-a-Judge with explicit reference answers derived from the
    Arborian Network dataset.  Unlike standard MCE, the judge receives the
    exact expected values and is asked to verify each one independently.

    This is the canonical example of how to extend MCE with a new provider:
    subclass EvalProvider, implement ``compute()`` and ``available_metrics()``,
    then register in evaluator._make_provider().

    Parameters
    ----------
    dataset_path : Path
        Path to mas-necessity.yaml (must contain ground_truth for mn1–mn4).
    llm_model : str
        Judge model, e.g. "azure/gpt-4o-mini".
    api_key_env : str
        Environment variable name for the API key.
    api_base : str | None
        OpenAI-compatible endpoint.  Falls back to OPENAI_API_BASE env var.
    """

    name = "trip_planner_gt"

    def __init__(
        self,
        dataset_path: Optional[Path] = None,
        llm_model: str = "gpt-4o-mini",
        api_key_env: str = "OPENAI_API_KEY",
        api_base: Optional[str] = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._dataset_cache: Optional[Dict] = None
        self._llm_model = llm_model
        self._api_key_env = api_key_env
        self._api_base = api_base
        self._client = None

    # ------------------------------------------------------------------
    # EvalProvider interface
    # ------------------------------------------------------------------

    def available_metrics(self) -> List[str]:
        return ["key_facts_accuracy", "claim_verification"]

    def compute(
        self,
        kg_path: Path,
        metric_names: List[str],
        *,
        response_agent_id: Optional[str] = None,
    ) -> Dict[str, MetricScore]:
        with kg_path.open(encoding="utf-8") as f:
            kg = json.load(f)

        output = self._extract_output(kg, response_agent_id)
        dataset = self._load_dataset()
        item_id = self._extract_item_id(kg_path, kg)
        item = self._find_item(dataset, item_id)

        if not item:
            return {
                name: {
                    "value": None,
                    "reasoning": f"Item {item_id} not found in dataset",
                    "error": f"Item {item_id} not found in dataset",
                }
                for name in metric_names
            }

        results: Dict[str, MetricScore] = {}
        for metric_name in metric_names:
            if metric_name == "key_facts_accuracy":
                results[metric_name] = self._metric_key_facts_accuracy(output, item, item_id)
            elif metric_name == "claim_verification":
                results[metric_name] = self._metric_claim_verification(output, item, item_id)
            else:
                results[metric_name] = {
                    "value": None,
                    "reasoning": f"Unknown metric: {metric_name}",
                    "error": f"Unknown metric: {metric_name}",
                }
        return results

    def compute_from_text(
        self,
        final_response: str,
        item_id: str,
        metric_names: List[str],
    ) -> Dict[str, MetricScore]:
        """Compute metrics directly from response text (OSS lightweight path).

        Bypasses KG normalization — works directly from the final response string.
        Used by the eval_trip_planner_gt pipeline step.
        """
        dataset = self._load_dataset()
        item = self._find_item(dataset, item_id)

        if not item:
            return {
                name: {
                    "value": None,
                    "reasoning": f"Item {item_id} not found in dataset",
                    "error": f"Item {item_id} not found in dataset",
                }
                for name in metric_names
            }

        results: Dict[str, MetricScore] = {}
        for metric_name in metric_names:
            if metric_name == "key_facts_accuracy":
                results[metric_name] = self._metric_key_facts_accuracy(final_response, item, item_id)
            elif metric_name == "claim_verification":
                results[metric_name] = self._metric_claim_verification(final_response, item, item_id)
            else:
                results[metric_name] = {
                    "value": None,
                    "reasoning": f"Unknown metric: {metric_name}",
                    "error": f"Unknown metric: {metric_name}",
                }
        return results

    # ------------------------------------------------------------------
    # Metric implementations
    # ------------------------------------------------------------------

    def _metric_key_facts_accuracy(
        self, output: str, item: Dict, item_id: str
    ) -> MetricScore:
        """Check whether the agent response contains the correct key facts.

        The judge receives the full ground_truth dict and scores how many
        key numerical/factual values appear correctly in the response.
        Returns 0.0–1.0 as fraction of key facts correctly stated.

        Applicable items: mn1, mn2, mn4 (structured numeric ground truths).
        """
        gt = item.get("ground_truth")
        if not gt or not isinstance(gt, dict):
            return {
                "value": None,
                "reasoning": f"No structured ground_truth for {item_id}",
                "error": None,
            }

        prompt_text = item.get("prompt", "")
        gt_yaml = yaml.dump(
            {k: v for k, v in gt.items() if not k.startswith("fare_breakdown")
             and "note" not in k},
            default_flow_style=False,
        )

        judge_prompt = f"""You are evaluating a trip-planning AI's response against a verified ground truth.

**TASK GIVEN TO THE AGENT:**
{prompt_text[:600]}

**VERIFIED GROUND TRUTH (computed from the Arborian Network dataset):**
{gt_yaml}

**AGENT'S RESPONSE:**
{output[:2500]}

**SCORING INSTRUCTIONS:**
Count how many of the key facts from the ground truth are CORRECTLY stated in the agent's response.
Key facts are numeric values (costs, totals) and factual conclusions (cheapest trip, optimal city, etc.).
Ignore commentary, activities, and non-factual parts.

Score = (number of correct key facts) / (total number of key facts in ground truth)

**OUTPUT FORMAT (strict JSON):**
{{
  "score": <float 0.0–1.0>,
  "correct_facts": ["<fact1>", "<fact2>", ...],
  "wrong_or_missing_facts": ["<fact3>", ...],
  "reasoning": "<2-3 sentences summarising what the agent got right and wrong>"
}}"""

        result = self._llm_judge(judge_prompt)
        return {
            "value": result.get("score"),
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "error": result.get("error"),
        }

    def _metric_claim_verification(
        self, output: str, item: Dict, item_id: str
    ) -> MetricScore:
        """Binary: did the agent correctly identify the claim violation?

        Specifically for mn3 (blogger's claim verification).
        Score 1.0 — agent correctly identifies the claim is WRONG and names
                     the timing violation on Verdantia→Luminos leg.
        Score 0.5 — agent says the claim is wrong but gives the wrong reason.
        Score 0.0 — agent says the claim is correct (hallucination/bias).

        For non-mn3 items, returns None (not applicable).
        """
        if item_id != "mn3":
            return {
                "value": None,
                "reasoning": f"claim_verification only applies to mn3, got {item_id}",
                "error": None,
            }

        gt = item.get("ground_truth", {})
        prompt_text = item.get("prompt", "")

        judge_prompt = f"""You are evaluating whether a trip-planning AI correctly verified a travel blogger's claim.

**TASK GIVEN TO THE AGENT:**
{prompt_text[:600]}

**VERIFIED GROUND TRUTH:**
- The blogger's claim is WRONG (claim_is_correct: false).
- The violated constraint is TIMING on the Verdantia → Luminos leg.
- Reason: Departing Celestia at 08:30, you arrive Verdantia at 11:30.
  No Standard morning departure (before noon) from Verdantia to Luminos is possible after that.
- The total cost ($135) is under $150 — cost constraint is satisfied.
- A corrected itinerary exists: use Express from Celestia (06:15) to arrive earlier.

**AGENT'S RESPONSE:**
{output[:2500]}

**SCORING:**
1.0 — Agent says the claim is WRONG AND correctly identifies timing as the violated constraint
      (specifically the Verdantia→Luminos connection timing issue).
0.5 — Agent says the claim is WRONG but gives an incorrect reason (e.g., says cost > $150).
0.0 — Agent says the claim is CORRECT (failed to detect the timing violation).

**OUTPUT FORMAT (strict JSON):**
{{
  "score": <0.0, 0.5, or 1.0>,
  "agent_verdict": "<correct|wrong|unclear>",
  "agent_stated_reason": "<brief summary of agent's stated violation, if any>",
  "reasoning": "<2-3 sentences explaining the score>"
}}"""

        result = self._llm_judge(judge_prompt)
        return {
            "value": result.get("score"),
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "error": result.get("error"),
        }

    # ------------------------------------------------------------------
    # LLM Judge infrastructure (mirrors AdversarialProvider pattern)
    # ------------------------------------------------------------------

    def _get_llm_client(self):
        if self._client is None:
            from openai import OpenAI
            api_key = os.environ.get(self._api_key_env)
            if not api_key:
                raise ValueError(f"API key not found in {self._api_key_env}")
            api_base = self._api_base or os.environ.get("OPENAI_API_BASE")
            self._client = OpenAI(api_key=api_key, base_url=api_base) if api_base else OpenAI(api_key=api_key)
        return self._client

    def _llm_judge(self, prompt: str, *, temperature: float = 0.0) -> Dict:
        client = self._get_llm_client()
        try:
            response = client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert evaluator for multi-agent system experiments. "
                            "Provide objective, structured assessments in JSON format. "
                            "Only evaluate what the agent's response explicitly states — "
                            "do not infer unstated facts."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
                **output_token_kwargs(self._llm_model),
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            if "score" not in result:
                raise ValueError("LLM response missing 'score' field")
            score = float(result.get("score", 0))
            result["score"] = max(0.0, min(1.0, score))
            return result
        except json.JSONDecodeError as e:
            return {"score": None, "reasoning": f"JSON parse error: {e}", "error": str(e)}
        except Exception as e:
            logger.error("LLM judge call failed: %s", e)
            return {"score": None, "reasoning": f"LLM error: {e}", "error": str(e)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_dataset(self) -> Dict:
        if self._dataset_cache is not None:
            return self._dataset_cache
        if not self._dataset_path:
            raise ValueError("dataset_path must be provided for TripPlannerGTProvider")
        if not self._dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found: {self._dataset_path}")
        self._dataset_cache = _load_dataset_file(self._dataset_path)
        return self._dataset_cache

    def _find_item(self, dataset: Dict, item_id: str) -> Optional[Dict]:
        for item in dataset.get("items", []):
            if item.get("id") == item_id:
                return item
        return None

    def _extract_item_id(self, kg_path: Path, kg: Dict) -> str:
        """Extract item_id from kg.json or infer from path."""
        if "item_id" in kg:
            return kg["item_id"]
        # Infer from path: .../mn1/r1/kg.json → "mn1"
        parts = kg_path.parts
        for i, part in enumerate(parts):
            if part.startswith("r") and part[1:].isdigit() and i > 0:
                return parts[i - 1]
        return kg_path.parent.parent.name

    def _extract_output(self, kg: Dict, response_agent_id: Optional[str] = None) -> str:
        """Extract the final response text from kg.json."""
        # Try direct final_response field
        if "final_response" in kg:
            return str(kg["final_response"])

        # Try session messages — last assistant message
        messages = kg.get("messages", kg.get("session", {}).get("messages", []))
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                    return " ".join(texts)
                return str(content)

        return str(kg)
