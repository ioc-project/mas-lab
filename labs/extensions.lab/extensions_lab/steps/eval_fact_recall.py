#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""eval_fact_recall — Provenance-based fact retrieval evaluation.

For each run in the benchmark, reads ``tool_call_end`` events for
``memory_search`` calls, extracts the doc_ids (keys) returned, and
compares them against ``expected_facts`` from the dataset.

Computes per-query and per-scenario aggregates:
  - precision     = |retrieved ∩ expected| / |retrieved|
  - recall        = |retrieved ∩ expected| / |expected|
  - F1            = 2 × precision × recall / (precision + recall)
  - hit_at_1      = 1 if at least one expected fact was retrieved
  - retrieved_ids = comma-separated list of retrieved doc_ids
  - expected_ids  = comma-separated list of expected fact keys

For k0 queries (expected_facts empty): precision is vacuously 1.0
if no facts were retrieved; 0.0 if any facts were retrieved (false positives).

Output
------
Two CSV files:

  ``fact_recall_runs.csv``   — one row per (scenario, item_id, run)
  ``fact_recall_summary.csv``— one row per (scenario, group); mean ± std of
                               precision/recall/F1 across all runs

Configuration
-------------

.. code-block:: yaml

    - name: eval-fact-recall
      type: eval_fact_recall
      config:
        dataset: ./datasets/fact-recall.yaml
        output_runs: "results/fact_recall_runs.csv"
        output_summary: "results/fact_recall_summary.csv"
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
)
from mas.lab.benchmark.pipeline import (
    PipelineStep,
    StepOutput,
    register_step_type,
)

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline import ExecutionContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

def _load_expected_facts(dataset_path: Path) -> dict[str, list[str]]:
    """Return {item_id: [fact_key, ...]} from the dataset YAML."""
    if not dataset_path.exists():
        logger.warning("eval_fact_recall: dataset not found at %s", dataset_path)
        return {}
    try:
        import yaml
        doc = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("eval_fact_recall: failed to parse dataset: %s", exc)
        return {}
    items = doc.get("spec", {}).get("items") or doc.get("items") or []
    out: dict[str, list[str]] = {}
    for it in items:
        iid = it.get("id") or it.get("item_id")
        facts = it.get("expected_facts") or []
        if iid is not None:
            out[str(iid)] = [str(f) for f in facts]
    return out


def _load_item_groups(dataset_path: Path) -> dict[str, str]:
    """Return {item_id: group}."""
    if not dataset_path.exists():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = doc.get("spec", {}).get("items") or doc.get("items") or []
    out: dict[str, str] = {}
    for it in items:
        iid = it.get("id") or it.get("item_id")
        grp = it.get("group") or "unknown"
        if iid is not None:
            out[str(iid)] = str(grp)
    return out


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------

def _load_events(run_dir: Path) -> list[dict]:
    """Load events.jsonl, following .run_ref symlinks if needed."""
    from mas.lab.benchmark.cache.trace_store import resolve_run_events_path

    events_path = resolve_run_events_path(run_dir)
    if events_path is None:
        return []
    out: list[dict] = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _extract_retrieved_keys(events: list[dict]) -> list[str]:
    """Return all doc_id/key values returned by memory_search tool calls.

    Handles two result shapes produced by MemorySearchTool:
      - ``results`` list (ToolContract path): each item has ``key`` field
      - ``items`` list (MemoryContract/read_memory path): each item has ``key`` field

    The trace event is wrapped by the runtime inline executor:
      {result: {status, result_mode, execution_mode, result: <actual payload>}}
    """
    keys: list[str] = []
    for ev in events:
        if ev.get("kind") != "tool_call_end":
            continue
        if ev.get("tool_name") not in ("memory-search", "memory_search"):
            continue
        result = ev.get("result", {})
        # Unwrap runtime envelope: {result: {result_mode, ..., result: <payload>}}
        while isinstance(result, dict) and "result" in result and len(result) <= 5:
            result = result["result"]
        if not isinstance(result, dict):
            continue
        # MemorySearchTool returns {results: [{text, source, score, key}, ...]}
        # read_memory / MemoryContract returns {items: [{key, content, metadata}, ...]}
        entries = result.get("results") or result.get("items") or []
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("doc_id")
            if not key:
                key = (item.get("metadata") or {}).get("key") or (item.get("metadata") or {}).get("doc_id")
            if key:
                # Normalise chunk_id → doc_id: strip trailing ":{digit(s)}" suffix
                _parts = str(key).rsplit(":", 1)
                if len(_parts) == 2 and _parts[1].isdigit():
                    key = _parts[0]
                keys.append(str(key))
    return keys


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _is_letta_run(events: list[dict]) -> bool:
    """Return True if the run used passive Letta injection.

    Requires both:
    1. No tool_call_end(memory_search) event.
    2. At least one context_part_contributed event contains '<memory_blocks>'
       — the exact XML injected by LettaCoreMemoryWrapper.collect_context().

    This avoids false-positives on baseline/no-tool runs that have generic
    context events without Letta block content.
    """
    has_tool_call = any(
        ev.get("kind") == "tool_call_end"
        and ev.get("tool_name") in ("memory-search", "memory_search")
        for ev in events
    )
    if has_tool_call:
        return False
    return any(
        ev.get("kind") == "context_part_contributed"
        and "<memory_blocks>" in (ev.get("content") or ev.get("text") or "")
        for ev in events
    )


def _compute_metrics(retrieved: list[str], expected: list[str]) -> dict:
    """Compute precision, recall, F1 for a single query."""
    retrieved_set = set(retrieved)
    expected_set = set(expected)

    if not expected_set:
        # k0: no fact expected — precision is 1 if nothing retrieved, 0 otherwise
        precision = 0.0 if retrieved_set else 1.0
        return {
            "precision": precision,
            "recall": 1.0,   # vacuously true
            "f1": precision,
            "hit_at_1": 1 if not retrieved_set else 0,
            "n_retrieved": len(retrieved_set),
            "n_expected": 0,
            "n_correct": 0,
        }

    n_correct = len(retrieved_set & expected_set)
    precision = n_correct / len(retrieved_set) if retrieved_set else 0.0
    recall = n_correct / len(expected_set)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "hit_at_1": 1 if n_correct >= 1 else 0,
        "n_retrieved": len(retrieved_set),
        "n_expected": len(expected_set),
        "n_correct": n_correct,
    }


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------

class EvalFactRecallStep(PipelineStep):
    """Provenance-based fact retrieval evaluation."""

    type = "eval_fact_recall"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        cfg = self.config

        # Resolve paths
        runs_dir = resolve_step_input_path(ctx, self.name, cfg, "runs_dir", ".")

        dataset_raw = cfg.get("dataset", "")
        dataset_path = Path(str(dataset_raw)).expanduser()
        if not dataset_path.is_absolute() and ctx.pipeline.config_path:
            dataset_path = (ctx.pipeline.config_path.parent / dataset_path).resolve()

        expected_by_item = _load_expected_facts(dataset_path)
        groups_by_item = _load_item_groups(dataset_path)

        # Letta configuration
        letta_scenarios: set[str] = set(cfg.get("letta_scenarios") or [])
        n_seeded: int = int(cfg.get("n_seeded_facts", 100))
        # All seeded fact keys: f001..f{n_seeded}
        all_seeded_keys: list[str] = [f"f{i:03d}" for i in range(1, n_seeded + 1)]

        rows: list[dict] = []
        for run_dir in sorted(runs_dir.glob("*/item*/r*")):
            if not run_dir.is_dir():
                continue
            parts = run_dir.parts
            scenario = parts[-3]
            item_raw = parts[-2]
            item_id = item_raw[len("item"):] if item_raw.startswith("item") else item_raw
            run_idx = parts[-1]

            if item_id not in expected_by_item:
                continue  # not a fact-recall item

            events = _load_events(run_dir)

            # Determine retrieved keys based on memory architecture:
            # - Letta (ContextContract): passive injection → all seeded facts are
            #   always in context; detect by scenario name or absence of
            #   tool_call_end(memory_search) + presence of <memory_blocks> content.
            # - Vector (ToolContract): sparse retrieval → read from tool spans
            if scenario in letta_scenarios or _is_letta_run(events):
                retrieved = list(all_seeded_keys)
            else:
                retrieved = _extract_retrieved_keys(events)
            expected = expected_by_item[item_id]
            metrics = _compute_metrics(retrieved, expected)

            rows.append({
                "scenario": scenario,
                "item_id": item_id,
                "run_idx": run_idx,
                "group": groups_by_item.get(item_id, "unknown"),
                "retrieved_ids": ",".join(sorted(set(retrieved))),
                "expected_ids": ",".join(sorted(expected)),
                **metrics,
            })

        df = pd.DataFrame(rows)

        # Write per-run CSV
        output_runs = resolve_step_artifact_path(
            ctx, None, self.name, "fact_recall_runs.csv", cfg,
            key="output_runs", default_subdir="results", name_keys=(), mkdir=True,
        )
        df.to_csv(output_runs, index=False)

        # Build summary: mean ± std per (scenario, group)
        files = [output_runs]
        summary_df = pd.DataFrame()
        output_summary = resolve_step_artifact_path(
            ctx, None, self.name, "fact_recall_summary.csv", cfg,
            key="output_summary", default_subdir="results", name_keys=(), mkdir=True,
        )

        if not df.empty:
            numeric_cols = ["precision", "recall", "f1", "hit_at_1", "n_retrieved", "n_expected", "n_correct"]
            summary_rows = []
            for (scenario, group), gdf in df.groupby(["scenario", "group"]):
                row: dict = {"scenario": scenario, "group": group, "n_runs": len(gdf)}
                for col in numeric_cols:
                    if col in gdf.columns:
                        row[f"{col}_mean"] = round(gdf[col].mean(), 4)
                        row[f"{col}_std"] = round(gdf[col].std(), 4)
                summary_rows.append(row)
            summary_df = pd.DataFrame(summary_rows)
            summary_df.to_csv(output_summary, index=False)
            files.append(output_summary)

        logger.info(
            "EvalFactRecallStep '%s': %d runs → runs=%s, summary=%s",
            self.name, len(df), output_runs, output_summary,
        )
        return StepOutput(
            data={"df": df, "summary_df": summary_df},
            files=files,
            metadata={"runs": len(df), "output_runs": str(output_runs), "output_summary": str(output_summary)},
        )


register_step_type("eval_fact_recall", EvalFactRecallStep)
