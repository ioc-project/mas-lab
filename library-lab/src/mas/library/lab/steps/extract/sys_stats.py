#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ExtractSysStatsStep — extract per-run plugin timing from sys_stats events.

Reads ``events.jsonl`` files for every scenario/item/run triple, finds the
``sys_stats`` event emitted by the runtime driver at session end, and writes a tidy CSV with one row per (scenario, item, run, agent,
plugin, hook) combination.

Output schema
-------------
scenario          str    Scenario folder name.
item              str    Item folder name (e.g. itemn1).
run               int    Run index (r<N> folder name → N).
agent_id          str    Agent identifier from the sys_stats event.
run_elapsed_ms    float  Total agent session wall-clock time (ms).
plugin_overhead_ms float Total time spent inside all plugins (ms).
llm_ms            float  Total time blocked on LLM responses (ms).
tool_ms           float  Total time executing tools (ms).
total_plugin_calls int   Total plugin hook invocations in this run.
total_llm_calls   int    Total LLM calls.
plugin_overhead_pct float plugin_overhead_ms / run_elapsed_ms × 100.
plugin            str    Plugin class name.
hook              str    Hook name (pre_llm_call, post_context_assembly, …).
role              str    ContractBinding role: "witness" | "transform" | "governance" | "".
contract          str    Contract the hook belongs to (e.g. "ModelContract").
calls             int    Number of invocations of this plugin×hook.
total_ms          float  Cumulative wall-clock for this plugin×hook (ms).
avg_ms            float  Mean per-call latency (ms).
max_ms            float  Peak latency (ms).
errors            int    Number of error outcomes.
denials           int    Number of policy-denial outcomes.

Configuration
-------------
output       str   Relative output path (default: ``sys_stats.csv`` in
                   ``<level folder>/results/``).
scenarios    list  Optional list of scenario IDs to include.
item_glob    str   Glob pattern for item directories (default: ``item*``).
run_glob     str   Glob pattern for run dirs (default: ``r*``).

Example YAML::

    - name: extract-sys-stats
      type: extract_sys_stats
      config: {}   # writes results/sys_stats.csv
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_root,
)

logger = logging.getLogger(__name__)


from mas.lab.benchmark.cache.trace_store import resolve_run_events_path as _resolve_events_path


def _run_index(run_dir: str) -> int:
    m = re.search(r"(\d+)", run_dir)
    return int(m.group(1)) if m else 0


def _extract_sys_stats_rows(
    scenario: str,
    item: str,
    run_idx: int,
    events_path: Path,
) -> List[Dict[str, Any]]:
    """Parse sys_stats events from one events.jsonl and return tidy rows."""
    rows: List[Dict[str, Any]] = []
    try:
        with open(events_path, encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
    except Exception as exc:
        logger.warning("Failed to read %s: %s", events_path, exc)
        return rows

    for evt in events:
        if evt.get("kind") != "sys_stats":
            continue
        stats: Dict[str, Any] = evt.get("stats", {})
        agent_id: str = evt.get("agent_id", "")
        run_elapsed_ms: float = stats.get("run_elapsed_ms", 0.0)
        plugin_overhead_ms: float = stats.get("plugin_overhead_ms", 0.0)
        llm_ms: float = stats.get("llm_ms", 0.0)
        tool_ms: float = stats.get("tool_ms", 0.0)
        total_plugin_calls: int = stats.get("total_plugin_calls", 0)
        total_llm_calls: int = stats.get("total_llm_calls", 0)
        overhead_pct: float = (
            round(plugin_overhead_ms / run_elapsed_ms * 100, 4)
            if run_elapsed_ms > 0
            else 0.0
        )

        base = {
            "scenario":            scenario,
            "item":                item,
            "run":                 run_idx,
            "agent_id":            agent_id,
            "run_elapsed_ms":      round(run_elapsed_ms, 3),
            "plugin_overhead_ms":  round(plugin_overhead_ms, 3),
            "llm_ms":              round(llm_ms, 3),
            "tool_ms":             round(tool_ms, 3),
            "total_plugin_calls":  total_plugin_calls,
            "total_llm_calls":     total_llm_calls,
            "plugin_overhead_pct": overhead_pct,
        }

        plugins: List[Dict[str, Any]] = stats.get("plugins", [])
        if not plugins:
            # Emit one summary row with blank plugin columns
            rows.append({**base, "plugin": "", "hook": "", "contract": "", "role": "",
                         "calls": 0, "total_ms": 0.0, "avg_ms": 0.0,
                         "max_ms": 0.0, "errors": 0, "denials": 0})
        else:
            for p in plugins:
                rows.append({
                    **base,
                    "plugin":      p.get("plugin", ""),
                    "hook":        p.get("hook", ""),
                    "contract":    p.get("contract", ""),
                    "role":        p.get("role", ""),
                    "calls":       p.get("calls", 0),
                    "total_ms":    round(p.get("total_ms", 0.0), 3),
                    "avg_ms":      round(p.get("avg_ms", 0.0), 3),
                    "max_ms":      round(p.get("max_ms", 0.0), 3),
                    "errors":      p.get("errors", 0),
                    "denials":     p.get("denials", 0),
                })
    return rows


class ExtractSysStatsStep(PipelineStep):
    """Extract per-plugin timing from sys_stats trace events."""

    type = "extract_sys_stats"

    async def execute(self, ctx: "Any") -> StepOutput:  # noqa: F821
        config = self.config

        output_dir = resolve_step_root(ctx, config, step=self.name)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "sys_stats.csv", config, default_subdir="results", mkdir=True
        )

        scenario_filter: Optional[List[str]] = config.get("scenarios")
        item_glob: str = config.get("item_glob", "item*")
        run_glob: str = config.get("run_glob", "r*")

        all_rows: List[Dict[str, Any]] = []

        for scenario_dir in sorted(output_dir.iterdir()):
            if not scenario_dir.is_dir():
                continue
            scenario = scenario_dir.name
            if scenario_filter and scenario not in scenario_filter:
                continue
            # Skip auxiliary directories
            if scenario in {"results", "data", "figures", "logs"}:
                continue

            for item_dir in sorted(scenario_dir.glob(item_glob)):
                if not item_dir.is_dir():
                    continue
                item = item_dir.name

                for run_dir in sorted(item_dir.glob(run_glob)):
                    if not run_dir.is_dir():
                        continue
                    run_idx = _run_index(run_dir.name)
                    events_path = _resolve_events_path(run_dir)
                    if events_path is None:
                        continue

                    # Capture run_hash for unified cache linkage
                    _run_hash = ""
                    run_ref_f = run_dir / ".run_ref"
                    if run_ref_f.exists():
                        try:
                            _run_hash = run_ref_f.read_text(encoding="utf-8").strip()
                        except Exception:
                            logger.debug('suppressed', exc_info=True)

                    rows = _extract_sys_stats_rows(scenario, item, run_idx, events_path)
                    for r in rows:
                        r["run_hash"] = _run_hash
                    all_rows.extend(rows)
                    if rows:
                        logger.debug(
                            "extracted %d plugin rows from %s/%s/r%s",
                            len(rows), scenario, item, run_idx,
                        )

        if not all_rows:
            logger.warning("ExtractSysStatsStep: no sys_stats events found under %s", output_dir)
        else:
            logger.info("ExtractSysStatsStep: %d rows extracted → %s", len(all_rows), output_path)

        # Write CSV
        fieldnames = [
            "scenario", "item", "run", "run_hash", "agent_id",
            "run_elapsed_ms", "plugin_overhead_ms", "llm_ms", "tool_ms",
            "total_plugin_calls", "total_llm_calls", "plugin_overhead_pct",
            "plugin", "hook", "contract", "role",
            "calls", "total_ms", "avg_ms", "max_ms",
            "errors", "denials",
        ]
        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)

        # Also return as DataFrame so downstream steps can use @extract-sys-stats
        try:
            import pandas as pd
            df = pd.DataFrame(all_rows, columns=fieldnames)
        except ImportError:
            df = None

        return StepOutput(
            data={"df": df, "path": str(output_path)},
            files=[output_path],
            metadata={"rows": len(all_rows)},
        )
