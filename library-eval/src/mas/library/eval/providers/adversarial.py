#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Adversarial evaluation provider — accuracy metrics for MAS necessity items.

Measures whether agents produce factually correct answers on hard but solvable
trip-planning queries drawn from the Arborian Network. Each metric compares the
agent's response against a pre-computed ground truth embedded in the dataset.

MAS advantage hypothesis: specialist agents (schedule_agent / concierge_agent)
cross-checking fares and timetables independently produce fewer hallucinations
than a single agent handling both in one pass.

Usage::

    from mas.library.eval import get_provider

    provider = get_provider("adversarial", llm_model="gpt-4o", api_key_env="OPENAI_API_KEY")
    scores = provider.compute(
        kg_path=Path("output/mn10/r1/kg.json"),
        metric_names=["budget_respected"],
    )

Metric IDs
----------
- ``budget_respected``  — mn10: correct route found, correct fare ($50), budget respected ($15 remaining)
- ``fare_accuracy``     — mn11: correct departure time (08:30), connection (12:45), total fare ($50)
- ``fare_error_caught`` — mn12: both inflated fares detected (C→V $35→$28, V→L $25→$22), correct total ($50)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from mas.library.eval.evaluator import EvalProvider, MetricScore
from mas.runtime.engine.llm_output_limits import output_token_kwargs
from mas.runtime.spec.source import load_dataset_items_file as _load_dataset_file

logger = logging.getLogger(__name__)


class AdversarialProvider(EvalProvider):
    """Evaluation provider for adversarial detection metrics.

    Uses LLM-as-a-Judge for robust semantic evaluation instead of pattern matching.

    Parameters
    ----------
    dataset_path : Path
        Path to the Dataset YAML containing ground truth and item IDs.
    llm_model : str
        LLM model for judge (e.g., "gpt-4o", "gpt-4o-mini")
    api_key_env : str
        Environment variable containing API key
    """

    name = "adversarial"

    def __init__(
        self,
        dataset_path: Optional[Path] = None,
        llm_model: str = "gpt-4o",
        api_key_env: str = "OPENAI_API_KEY",
        api_base: Optional[str] = None,
    ) -> None:
        """Initialize provider with dataset path and LLM config.

        Args:
            dataset_path: Path to Dataset YAML. If None, will be auto-detected.
            llm_model: LLM model for judge evaluation
            api_key_env: Environment variable name for API key
            api_base: API base URL (if None, uses OpenAI default, OPENAI_BASE_URL, then OPENAI_API_BASE)
        """
        self._dataset_path = dataset_path
        self._dataset_cache: Optional[Dict] = None
        self._llm_model = llm_model
        self._api_key_env = api_key_env
        self._api_base = api_base
        self._client = None  # Lazy init on first use

    def compute(
        self,
        kg_path: Path,
        metric_names: List[str],
        *,
        response_agent_id: Optional[str] = None,
    ) -> Dict[str, MetricScore]:
        """Compute adversarial metrics from kg.json.

        Args:
            kg_path: Path to kg.json file
            metric_names: List of metric IDs to compute
            response_agent_id: Optional agent ID for response extraction

        Returns:
            Mapping of metric_id → {value, reasoning, error}
        """
        # Load KG and extract output
        with kg_path.open(encoding="utf-8") as f:
            kg = json.load(f)

        output = self._extract_output(kg, response_agent_id)

        # Load dataset and find item
        dataset = self._load_dataset(kg_path)
        item_id = self._extract_item_id(kg_path, kg)
        item = self._find_item(dataset, item_id)

        if not item:
            # Return error for all metrics if item not found
            return {
                name: {
                    "value": None,
                    "reasoning": f"Item {item_id} not found in dataset",
                    "error": f"Item {item_id} not found in dataset",
                }
                for name in metric_names
            }

        # Compute requested metrics
        results: Dict[str, MetricScore] = {}
        for metric_name in metric_names:
            if metric_name == "budget_respected":
                results[metric_name] = self._metric_budget_respected(output, item, item_id)
            elif metric_name == "fare_accuracy":
                results[metric_name] = self._metric_fare_accuracy(output, item, item_id)
            elif metric_name == "fare_error_caught":
                results[metric_name] = self._metric_fare_error_caught(output, item, item_id)
            else:
                results[metric_name] = {
                    "value": None,
                    "reasoning": f"Unknown adversarial metric: {metric_name}",
                    "error": f"Unknown metric: {metric_name}",
                }

        return results

    def compute_from_text(
        self,
        final_response: str,
        item_id: str,
        metric_names: List[str],
    ) -> Dict[str, MetricScore]:
        """Compute adversarial metrics directly from response text (OSS lightweight path).

        This method bypasses KG normalization and works directly from the final
        response text, making it suitable for paper experiments without full
        graph pipeline.

        Args:
            final_response: The agent's final response text
            item_id: Item identifier (mn10, mn11, mn12)
            metric_names: List of metric IDs to compute

        Returns:
            Mapping of metric_id → {value, reasoning, error}
        """
        # Load dataset and find item (uses dataset_path from __init__)
        dataset = self._load_dataset_from_path()
        item = self._find_item(dataset, item_id)

        if not item:
            # Return error for all metrics if item not found
            return {
                name: {
                    "value": None,
                    "reasoning": f"Item {item_id} not found in dataset",
                    "error": f"Item {item_id} not found in dataset",
                }
                for name in metric_names
            }

        # Compute requested metrics using final_response as output
        results: Dict[str, MetricScore] = {}
        for metric_name in metric_names:
            if metric_name == "budget_respected":
                results[metric_name] = self._metric_budget_respected(final_response, item, item_id)
            elif metric_name == "fare_accuracy":
                results[metric_name] = self._metric_fare_accuracy(final_response, item, item_id)
            elif metric_name == "fare_error_caught":
                results[metric_name] = self._metric_fare_error_caught(final_response, item, item_id)
            else:
                results[metric_name] = {
                    "value": None,
                    "reasoning": f"Unknown adversarial metric: {metric_name}",
                    "error": f"Unknown metric: {metric_name}",
                }

        return results

    def available_metrics(self) -> List[str]:
        """Return list of available adversarial metrics."""
        return [
            "budget_respected",
            "fare_accuracy",
            "fare_error_caught",
        ]

    # -------------------------------------------------------------------------
    # LLM Judge infrastructure
    # -------------------------------------------------------------------------

    def _get_llm_client(self):
        """Get or create OpenAI client (lazy init)."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError("OpenAI client not available. Install with: pip install openai") from exc

            api_key = os.environ.get(self._api_key_env)
            if not api_key:
                raise ValueError(f"API key not found in environment variable {self._api_key_env}")

            # Use provided api_base or fall back to OPENAI_BASE_URL, then the older OPENAI_API_BASE
            api_base = self._api_base or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")

            if api_base:
                self._client = OpenAI(api_key=api_key, base_url=api_base)
            else:
                self._client = OpenAI(api_key=api_key)

        return self._client

    def _llm_judge(self, prompt: str, *, temperature: float = 0.0) -> Dict:
        """Call LLM judge and parse JSON response.

        Args:
            prompt: Evaluation prompt (should request JSON output)
            temperature: Sampling temperature (0.0 for deterministic)

        Returns:
            Parsed JSON response with {"score": float, "reasoning": str}
        """
        client = self._get_llm_client()

        try:
            response = client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert evaluator for multi-agent system experiments. "
                        "Provide objective, structured assessments in JSON format.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
                **output_token_kwargs(self._llm_model),
            )

            content = response.choices[0].message.content
            result = json.loads(content)

            # Validate response structure
            if "score" not in result:
                raise ValueError("LLM response missing 'score' field")
            if "reasoning" not in result:
                result["reasoning"] = "No reasoning provided"

            # Normalize score to 0.0-1.0
            score = float(result["score"])
            if not (0.0 <= score <= 1.0):
                logger.warning(f"Score {score} out of range [0.0, 1.0], clipping")
                score = max(0.0, min(1.0, score))

            result["score"] = score
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {
                "score": None,
                "reasoning": f"JSON parse error: {e}",
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"LLM judge call failed: {e}")
            return {
                "score": None,
                "reasoning": f"LLM error: {e}",
                "error": str(e),
            }

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------

    def _load_dataset_from_path(self) -> Dict:
        """Load dataset from the configured dataset_path (YAML or JSON)."""
        if self._dataset_cache is not None:
            return self._dataset_cache

        if not self._dataset_path:
            raise ValueError("dataset_path must be provided when using compute_from_text()")

        if not self._dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found: {self._dataset_path}")

        self._dataset_cache = _load_dataset_file(self._dataset_path)
        return self._dataset_cache

    # -------------------------------------------------------------------------
    # Metric implementations
    # -------------------------------------------------------------------------

    def _metric_budget_respected(self, output: str, item: Dict, item_id: str) -> MetricScore:
        """Check correct route + fare + budget for mn10 using LLM-as-a-Judge.

        mn10: Train-only Standard-class trip from Celestia visiting one
        forest_belt city (Verdantia, $28) and one plains city (Luminos, $22).
        Ground truth: total $50, $15 remaining from $65 budget.

        Returns:
            1.0 — correct route (C→V→L), correct total $50, correct remaining $15
            0.5 — correct route but wrong fare or wrong remaining budget
            0.0 — wrong route or hallucinated fare
        """
        if item_id != "mn10":
            return {
                "value": None,
                "reasoning": f"Metric only applies to mn10, got {item_id}",
                "error": None,
            }

        prompt_text = item.get("prompt", "")

        judge_prompt = f"""You are evaluating whether an AI trip-planning agent \
found the correct budget-constrained route.

**TASK (given to the agent):**
{prompt_text[:800]}

**GROUND TRUTH:**
- Correct route: Celestia → Verdantia → Luminos
- Celestia → Verdantia Standard fare: $28
- Verdantia → Luminos Standard fare: $22
- Total fare: $50
- Budget remaining from $65: $15
- This is the ONLY valid train-only Standard route satisfying both region constraints.

**AGENT'S RESPONSE:**
{output[:2000]}

**SCORING CRITERIA:**

Score 1.0 — Full accuracy:
- Identifies Celestia → Verdantia → Luminos as the route
- States total cost $50 (or itemises $28 + $22)
- States budget remaining $15 (or equivalent correct subtraction)

Score 0.5 — Correct route, wrong arithmetic:
- Identifies the correct route (C→V→L)
- But gives a wrong total (e.g. $42+$22=$64, or $55) or wrong remaining budget

Score 0.0 — Wrong route or fare hallucination:
- Proposes a different or non-existent route
- Or gives a total that does not correspond to Standard-class fares
- Or claims no valid route exists when one does

**OUTPUT FORMAT (strict JSON):**
{{
  "score": <0.0, 0.5, or 1.0>,
  "reasoning": "<2-3 sentences: route identified, total fare reported, remaining budget reported>"
}}"""

        result = self._llm_judge(judge_prompt)
        return {
            "value": result.get("score"),
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "error": result.get("error"),
        }

    def _metric_fare_accuracy(self, output: str, item: Dict, item_id: str) -> MetricScore:
        """Check schedule + fare accuracy for mn11 using LLM-as-a-Judge.

        mn11: Weekday Standard-class Celestia→Luminos before 09:00 departure.
        Ground truth: 08:30 departure, Verdantia connection at 12:45, total $50.

        Returns:
            1.0 — correct departure (08:30), correct connection (12:45), correct fare ($50)
            0.5 — correct departure OR correct fare, but not both
            0.0 — wrong departure AND wrong fare
        """
        if item_id != "mn11":
            return {
                "value": None,
                "reasoning": f"Metric only applies to mn11, got {item_id}",
                "error": None,
            }

        prompt_text = item.get("prompt", "")

        judge_prompt = f"""You are evaluating whether an AI trip-planning agent \
gave the correct schedule and fare for a constrained journey.

**TASK (given to the agent):**
{prompt_text[:800]}

**GROUND TRUTH:**
- Earliest Standard departure from Celestia before 09:00: 08:30
  (06:15 is Express, not Standard — must be excluded)
- Connection city: Verdantia
- Onward Standard departure from Verdantia: 12:45
  (10:30 is Express; next Standard after ~11:30 arrival is 12:45)
- Approximate arrival in Luminos: 14:45
- Total Standard fare: $28 (C→V) + $22 (V→L) = $50

**AGENT'S RESPONSE:**
{output[:2000]}

**SCORING CRITERIA:**

Score 1.0 — Full accuracy:
- States departure from Celestia: 08:30 (or equivalent)
- States connection in Verdantia, onward at 12:45 (or close equivalent)
- States total fare: $50 (or itemises $28 + $22)

Score 0.5 — Partial accuracy (one dimension correct):
- Correct departure time (08:30) but wrong fare
- OR correct total fare ($50) but wrong departure or connection time
- OR identifies the correct route/connection city but no fare given

Score 0.0 — Both wrong:
- Wrong departure time (e.g. 06:15 Express mistaken for Standard)
  AND wrong total fare
- Or completely wrong route

**OUTPUT FORMAT (strict JSON):**
{{
  "score": <0.0, 0.5, or 1.0>,
  "reasoning": "<2-3 sentences: departure time reported, connection time reported, fare reported>"
}}"""

        result = self._llm_judge(judge_prompt)
        return {
            "value": result.get("score"),
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "error": result.get("error"),
        }

    def _metric_fare_error_caught(self, output: str, item: Dict, item_id: str) -> MetricScore:
        """Check whether both inflated fares are caught for mn12 using LLM-as-a-Judge.

        mn12: An online booking site shows two inflated fares.
        Ground truth: C→V Standard $28 (shown as $35), V→L Standard $22 (shown as $25).
        Correct total: $50 (shown as $60).

        Returns:
            1.0 — both fare errors caught, correct total $50 stated
            0.5 — one of the two fare errors caught
            0.0 — no errors caught (accepted inflated prices)
        """
        if item_id != "mn12":
            return {
                "value": None,
                "reasoning": f"Metric only applies to mn12, got {item_id}",
                "error": None,
            }

        prompt_text = item.get("prompt", "")

        judge_prompt = f"""You are evaluating whether an AI trip-planning agent \
caught inflated fares in an online booking quote.

**TASK (given to the agent):**
{prompt_text[:800]}

**GROUND TRUTH (correct Arborian Network fares):**
- Celestia → Verdantia Standard train: $28  (site shows $35 — overcharged by $7)
- Verdantia → Luminos Standard train: $22   (site shows $25 — overcharged by $3)
- Correct total: $50                        (site shows $60)

**AGENT'S RESPONSE:**
{output[:2000]}

**SCORING CRITERIA:**

Score 1.0 — Both errors caught:
- Identifies Celestia→Verdantia should be $28, not $35
- Identifies Verdantia→Luminos should be $22, not $25
- States correct total of $50

Score 0.5 — One error caught:
- Catches one of the two inflated fares but misses the other
- May or may not give the correct total

Score 0.0 — No errors caught:
- Accepts both $35 and $25 as correct (or doesn't check)
- States $60 as the correct total

**OUTPUT FORMAT (strict JSON):**
{{
  "score": <0.0, 0.5, or 1.0>,
  "reasoning": "<2-3 sentences: which fare errors were caught, correct total stated?>"
}}"""

        result = self._llm_judge(judge_prompt)
        return {
            "value": result.get("score"),
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "error": result.get("error"),
        }

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------

    def _extract_output(self, kg: Dict, response_agent_id: Optional[str]) -> str:
        """Extract final agent output from kg.json."""
        nodes = kg.get("nodes", [])

        # Find root AgentCall node
        agent_calls = [n for n in nodes if n.get("type") == "AgentCall"]

        if not agent_calls:
            logger.warning("No AgentCall nodes found in KG")
            return ""

        # If response_agent_id specified, find that agent's call
        if response_agent_id:
            for call in agent_calls:
                if call.get("agentId") == response_agent_id:
                    return call.get("outputContent", "")

        # Otherwise, find root call (no incoming contains edge)
        edges = kg.get("edges", [])
        contains_targets = {e["target"] for e in edges if e.get("type") == "contains"}

        root_calls = [c for c in agent_calls if c.get("id") not in contains_targets]

        if root_calls:
            # Take earliest by startTime
            root = min(root_calls, key=lambda c: c.get("startTime", ""))
            return root.get("outputContent", "")

        # Fallback: take first agent call
        return agent_calls[0].get("outputContent", "")

    def _extract_item_id(self, kg_path: Path, kg: Dict) -> str:
        """Extract item ID from kg.json or path.

        Tries:
        1. kg["session"]["item_id"]
        2. kg["metadata"]["item_id"]
        3. Parse from path: .../item_id/run_*/ → item_id
        """
        # Try session metadata
        session = kg.get("session", {})
        if "item_id" in session:
            return session["item_id"]

        # Try top-level metadata
        metadata = kg.get("metadata", {})
        if "item_id" in metadata:
            return metadata["item_id"]

        # Parse from path: .../mn10/r0/kg.json → mn10
        parts = kg_path.parts
        for i, part in enumerate(parts):
            if part.startswith("mn") and i + 1 < len(parts):
                return part

        logger.warning(f"Could not extract item_id from {kg_path}")
        return "unknown"

    def _load_dataset(self, kg_path: Path) -> Dict:
        """Load dataset from cache or disk."""
        if self._dataset_cache:
            return self._dataset_cache

        if self._dataset_path and self._dataset_path.exists():
            dataset_path = self._dataset_path
        else:
            # Auto-detect: look for datasets/mas-necessity.yaml in parent dirs
            current = kg_path.parent
            dataset_path = None

            # Try ../../../datasets/mas-necessity.yaml (standard structure)
            for _ in range(5):  # Search up to 5 levels
                for candidate in (
                    current / "datasets" / "mas-necessity.yaml",
                    current / "mas-necessity.yaml",
                ):
                    if candidate.exists():
                        dataset_path = candidate
                        break
                if dataset_path:
                    break
                current = current.parent

        if not dataset_path or not dataset_path.exists():
            logger.warning(f"Dataset not found near {kg_path}")
            self._dataset_cache = {"items": []}
            return self._dataset_cache

        data = _load_dataset_file(dataset_path)
        self._dataset_cache = data
        return data

    def _find_item(self, dataset, item_id: str) -> Optional[Dict]:
        """Find item in dataset by ID.

        Args:
            dataset: Either a list of items or a dict with "items" key
            item_id: Item identifier to find

        Returns:
            Item dict or None if not found
        """
        # Handle both formats: list directly or dict with "items" key
        if isinstance(dataset, list):
            items = dataset
        elif isinstance(dataset, dict):
            items = dataset.get("items", [])
        else:
            logger.warning("Dataset is neither list nor dict: %s", type(dataset))
            return None

        for item in items:
            if item.get("id") == item_id:
                return item
        return None
