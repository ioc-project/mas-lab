#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""attribution_flow — Build a per-run attribution table from run traces + metrics.

Walks ``<runs_dir>/<scenario>/item*/r*/`` and for each run computes four
categorical attributes used to localise *where* quality is gained or lost:

* ``scenario``         — taken from the directory layout
* ``query_group``      — read from dataset overlay (recall / combination /
  neutral / guardrail), with a sensible fallback (``unknown``)
* ``memory_outcome``   — ``hit`` if any ``memory_*`` / ``memory_search`` or
  ``context_part_contributed`` with ``source=memory_*`` span fired in the
  trace; ``miss`` if a memory plugin was wired (scenario name contains
  ``memory`` or ``letta``) but no memory event was emitted; ``n/a`` for the
  baseline scenarios.
* ``tool_outcome``     — ``ok`` if every ``tool_call_start`` has a matching
  ``tool_call_end`` with status != error; ``error`` otherwise; ``none`` if
  no tool was called.
* ``answer_outcome``   — ``correct`` if ``goal_success_rate >= 0.5`` in the
  run's ``metrics.json``; ``wrong`` if < 0.5; ``unscored`` if missing.

Writes a tidy CSV and exposes the DataFrame for ``sankey_flow``.

Configuration
-------------

.. code-block:: yaml

    - name: attribution-data
      type: attribution_flow
      config:
        dataset: ./datasets/extensions-queries.yaml
        output: "results/attribution.csv"
"""
from __future__ import annotations

import json
import logging
import re
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

_MEM_SCENARIO_RE = re.compile(r"memory|letta", re.IGNORECASE)


def _classify_memory(events: list[dict], scenario: str) -> str:
    has_memory_plugin = bool(_MEM_SCENARIO_RE.search(scenario))
    for ev in events:
        kind = str(ev.get("kind", ""))
        name = str(ev.get("name", "") or ev.get("tool_name", ""))
        source = str(ev.get("source", ""))
        if "memory" in kind.lower() or "memory" in name.lower() or source.startswith("memory"):
            return "hit"
        # Letta blocks land as context_part_contributed with source=letta_block
        if kind == "context_part_contributed" and ("letta" in source.lower() or source.startswith("memory")):
            return "hit"
    if has_memory_plugin:
        return "miss"
    return "n/a"


def _classify_tool(events: list[dict]) -> str:
    starts = 0
    errs = 0
    for ev in events:
        k = str(ev.get("kind", ""))
        if k == "tool_call_start":
            starts += 1
        elif k == "tool_call_end":
            status = str(ev.get("status", "ok")).lower()
            if "error" in status or "fail" in status:
                errs += 1
    if starts == 0:
        return "none"
    return "error" if errs > 0 else "ok"


def _classify_answer(metrics_path: Path) -> str:
    if not metrics_path.exists():
        return "unscored"
    try:
        doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception:
        return "unscored"
    session = doc.get("session", {}) if isinstance(doc, dict) else {}
    entry = session.get("goal_success_rate") if isinstance(session, dict) else None
    if not isinstance(entry, dict):
        return "unscored"
    v = entry.get("value")
    if v is None:
        return "unscored"
    try:
        return "correct" if float(v) >= 0.5 else "wrong"
    except (TypeError, ValueError):
        return "unscored"


def _load_dataset_groups(path: Path) -> dict[str, str]:
    """Return {item_id: group}."""
    if not path.exists():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = doc.get("items") or doc.get("queries") or []
    out: dict[str, str] = {}
    for it in items:
        iid = it.get("id") or it.get("item_id")
        grp = it.get("group") or it.get("tag") or "unknown"
        if iid:
            out[str(iid)] = str(grp)
    return out


def _load_events(p: Path) -> list[dict]:
    out: list[dict] = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


class AttributionFlowStep(PipelineStep):
    """Build the (scenario, group, mem, tool, answer) attribution table."""

    type = "attribution_flow"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        cfg = self.config
        runs_dir = resolve_step_input_path(ctx, self.name, cfg, "runs_dir", ".")

        dataset_path_raw = cfg.get("dataset", "")
        dataset_path = Path(str(dataset_path_raw)).expanduser() if dataset_path_raw else None
        if dataset_path and not dataset_path.is_absolute() and ctx.pipeline.config_path:
            dataset_path = (ctx.pipeline.config_path.parent / dataset_path).resolve()
        groups = _load_dataset_groups(dataset_path) if dataset_path else {}

        rows: list[dict] = []
        for run_dir in sorted(runs_dir.glob("*/item*/r*")):
            if not run_dir.is_dir():
                continue
            parts = run_dir.parts
            scenario = parts[-3]
            item_id = parts[-2][len("item"):] if parts[-2].startswith("item") else parts[-2]
            run_idx = parts[-1]
            events = _load_events(run_dir / "traces" / "events.jsonl")
            row = {
                "scenario": scenario,
                "item_id": item_id,
                "run_idx": run_idx,
                "query_group": groups.get(item_id, "unknown"),
                "memory_outcome": _classify_memory(events, scenario),
                "tool_outcome": _classify_tool(events),
                "answer_outcome": _classify_answer(run_dir / "metrics.json"),
            }
            rows.append(row)

        if not rows:
            logger.warning("AttributionFlowStep '%s': no runs found under %s", self.name, runs_dir)
            return StepOutput(data={"df": None}, metadata={"warning": "empty"})

        df = pd.DataFrame(rows)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "attribution.csv", cfg, name_keys=(), mkdir=True
        )
        df.to_csv(output_path, index=False)
        logger.info("AttributionFlowStep '%s': %d runs → %s", self.name, len(df), output_path)
        return StepOutput(
            data={"df": df},
            files=[output_path],
            metadata={"runs": len(df), "output": str(output_path)},
        )


register_step_type("attribution_flow", AttributionFlowStep)
