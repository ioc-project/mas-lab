#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline steps for MCE evaluation in mas-lab benchmarks."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep
from mas.library.eval.mce import (
    compute_session_metrics,
    build_session_from_trace,
)

logger = logging.getLogger(__name__)


class EvalMceStep(PipelineStep):
    """Evaluate MAS output quality using MCE metrics.

    This step computes LLM-as-judge metrics on trace files produced by
    previous pipeline steps (e.g., run-mas).

    Configuration::

        - step: eval-mce
          metrics:
            - goal_success_rate
            - groundedness
            - response_completeness
          model: azure/gpt-4o
          api_base: https://api.openai.com/v1
          api_key_env: OPENAI_API_KEY
          output_file: metrics.json  # optional, default: metrics.json

    Input:
        - traces/events.jsonl (produced by run-mas)

    Output:
        - metrics.json (MCE metric results)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.metrics: List[str] = config.get("metrics", ["goal_success_rate"])
        self.model: str = config.get("model", "azure/gpt-4o")
        self.api_base: str = config.get("api_base", "https://api.openai.com/v1")
        self.api_key_env: str = config.get("api_key_env", "OPENAI_API_KEY")
        self.output_file: str = config.get("output_file", "metrics.json")

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute MCE evaluation step.

        Args:
            context: Pipeline context with:
                - work_dir: Path to scenario work directory
                - item_id: Item identifier
                - scenario: Scenario name
                - run_id: Run identifier

        Returns:
            Updated context with:
                - metrics_file: Path to metrics.json
                - metrics: Dict of computed metrics
        """
        work_dir = Path(context["work_dir"])
        item_id = context.get("item_id", "unknown")
        scenario = context.get("scenario", "unknown")

        # Find trace file
        trace_path = work_dir / "traces" / "events.jsonl"
        if not trace_path.exists():
            logger.warning(f"Trace file not found: {trace_path}")
            return context

        # Build LLM config
        import os
        llm_config = {
            "LLM_MODEL_NAME": self.model,
            "LLM_BASE_MODEL_URL": self.api_base,
            "LLM_API_KEY": os.environ.get(self.api_key_env, ""),
        }

        if not llm_config["LLM_API_KEY"]:
            logger.error(f"Environment variable {self.api_key_env} not set")
            return context

        # Load session
        try:
            session = build_session_from_trace(trace_path)
        except Exception as exc:
            logger.error(f"Failed to load trace {trace_path}: {exc}")
            return context

        # Compute metrics (SessionEntity API uses CamelCase class names)
        try:
            from mas.library.eval.mce.catalog import resolve_metric

            class_names = [resolve_metric(n).camel for n in self.metrics]
            results = await compute_session_metrics(session, class_names, llm_config)
        except Exception as exc:
            logger.error(f"Metric computation failed: {exc}", exc_info=True)
            return context

        # Build metrics document
        metrics_doc = _build_metrics_document(
            item_id=item_id,
            scenario=scenario,
            results=results,
        )

        # Write to file
        metrics_file = work_dir / self.output_file
        with metrics_file.open("w", encoding="utf-8") as fh:
            json.dump(metrics_doc, fh, indent=2)

        logger.info(f"Wrote metrics to {metrics_file}")

        # Update context
        context["metrics_file"] = str(metrics_file)
        context["metrics"] = metrics_doc

        return context


def _build_metrics_document(
    item_id: str,
    scenario: str,
    results: List[Any],
) -> Dict[str, Any]:
    """Build canonical metrics.json document from MCE results.

    Format matches the existing mas-lab metrics.json schema for compatibility
    with analysis and plotting tools.
    """
    session_metrics = {}
    for result in results:
        session_metrics[result.metric_name] = {
            "value": result.value,
            "reasoning": result.reasoning or "",
            "error": result.error_message if not result.success else None,
            "success": result.success,
        }

    return {
        "schema_version": "1",
        "engine": "mce",
        "item_id": item_id,
        "scenario": scenario,
        "session": session_metrics,
        "agents": {},  # Reserved for future per-agent metrics
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
