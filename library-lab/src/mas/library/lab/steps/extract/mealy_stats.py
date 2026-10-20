#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ExtractMealyStatsStep — extract per-run, per-agent Mealy machine statistics
from events.jsonl trace files.

This step derives measurements directly from the event stream without invoking
any LLM judge. It covers every observable aspect of the state machine:
execution spans, LLM I/O, tool calls, SM processing overhead, governance
checks (per hook × outcome), routing, and context assembly.

Output schema — ``mealy_stats.csv``
------------------------------------
Per-row: one agent execution span within a run.

scenario              str    Scenario folder name.
item                  str    Item folder name.
item_group            str    nominal / fault / guardrail / recall / combination.
run                   int    Run index (from r<N> folder name).
agent_id              str    Agent identifier.
execution_ms          float  Wall-clock for this agent invocation
                             (execution_end.timestamp − execution_start.timestamp
                             matched by call_id; 0 if unpaired).
llm_calls             int    llm_call_start events for this agent in this span.
llm_total_ms          float  Sum of llm_call_end.latency_ms for this agent.
llm_mean_ms           float  llm_total_ms / llm_calls (0 if no calls).
tool_calls            int    tool_call_start events.
tool_total_ms         float  Sum of tool_call_end.latency_ms.
tool_mean_ms          float  tool_total_ms / tool_calls (0 if no calls).
sm_calls              int    processing_call_end events (SM context injection).
sm_total_ms           float  Sum of processing_call_end.latency_ms.
gov_pre_execution_n   int    governance_event count at hook pre_execution.
gov_pre_execution_fired int  firings at pre_execution.
gov_pre_llm_call_n    int    governance_event count at hook pre_llm_call.
gov_pre_llm_call_fired int   firings at pre_llm_call.
gov_pre_tool_call_n   int    governance_event count at hook pre_tool_call.
gov_pre_tool_call_fired int  firings at pre_tool_call.
gov_pre_agent_comm_n  int    governance_event count at hook pre_agent_communication.
gov_pre_agent_comm_fired int firings at pre_agent_communication.
gov_user_output_n     int    governance_event count at hook user_output.
gov_user_output_fired int    firings at user_output.
gov_other_n           int    governance_event count at other hooks.
gov_other_fired       int    firings at other hooks.
routing_sent          int    Routing events where source_agent_id == agent_id.
routing_received      int    Routing events where target_agent_id == agent_id.
context_parts         int    context_part_contributed events for this agent.
context_tokens        int    Sum of token_estimate across context_part_contributed.

A companion per-run JSON artifact is also written at::

    <run_dir>/mealy_stats.json

containing the same data in a nested dict for direct programmatic use.

Configuration
-------------
output       str   Relative output path (default: ``mealy_stats.csv`` inside
                   ``<level folder>/results/``).
scenarios    list  Optional list of scenario IDs to include.
item_glob    str   Glob pattern for item directories (default: ``item*``).
run_glob     str   Glob pattern for run directories (default: ``r*``).
write_per_run_json  bool  Write per-run mealy_stats.json (default: true).

Example YAML::

    - name: extract-mealy-stats
      type: extract_mealy_stats
      config: {}   # writes results/mealy_stats.csv
"""

import csv
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_root,
)

logger = logging.getLogger(__name__)

_GROUP_MAP = [
    ("itemn", "nominal"),
    ("itemf", "fault"),
    ("itemr", "recall"),
    ("itemc", "combination"),
    ("itemg", "guardrail"),
]

_GOV_HOOKS = [
    "pre_execution",
    "pre_llm_call",
    "pre_tool_call",
    "pre_agent_communication",
    "user_output",
]


def _item_group(item: str) -> str:
    for prefix, group in _GROUP_MAP:
        if item.startswith(prefix):
            return group
    return "other"


def _run_index(run_dir: str) -> int:
    m = re.search(r"(\d+)", run_dir)
    return int(m.group(1)) if m else 0


from mas.lab.benchmark.cache.trace_store import resolve_run_events_path as _resolve_events_path
def _extract_mealy_rows(
    scenario: str,
    item: str,
    run_idx: int,
    events_path: Path,
    write_per_run_json: bool,
) -> List[Dict[str, Any]]:
    """Parse one events.jsonl and return tidy rows, one per agent execution."""
    try:
        with open(events_path, encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
    except Exception as exc:
        logger.warning("Failed to read %s: %s", events_path, exc)
        return []

    item_group = _item_group(item)

    # ── Execution spans: pair execution_start/end by call_id ─────────────────
    ex_starts: Dict[str, Dict] = {}
    ex_ends:   Dict[str, Dict] = {}
    for e in events:
        if e.get("kind") == "execution_start":
            ex_starts[e["call_id"]] = e
        elif e.get("kind") == "execution_end":
            ex_ends[e["call_id"]] = e

    # All agent IDs seen in any execution span
    all_agents = sorted({e["agent_id"] for e in ex_starts.values()})

    # ── Per-agent aggregation ─────────────────────────────────────────────────
    # Build agent → execution call_id mapping (an agent can be invoked >1 times)
    # We group by agent_id, summing over all spans for that agent in this file.

    def _agent_of(e: Dict) -> str:
        return e.get("agent_id", "")

    # Execution spans duration per agent (sum over multiple invocations)
    exec_ms: Dict[str, float] = defaultdict(float)
    for cid, es in ex_starts.items():
        agent = es.get("agent_id", "")
        ee = ex_ends.get(cid)
        if ee:
            exec_ms[agent] += (ee["timestamp"] - es["timestamp"]) * 1000.0

    # LLM calls: latency from llm_call_end.latency_ms, grouped by agent_id
    llm_calls_n:   Dict[str, int]   = defaultdict(int)
    llm_total_ms:  Dict[str, float] = defaultdict(float)
    for e in events:
        if e.get("kind") == "llm_call_end" and "latency_ms" in e:
            a = _agent_of(e)
            llm_calls_n[a]  += 1
            llm_total_ms[a] += e["latency_ms"]

    # Tool calls: latency from tool_call_end.latency_ms
    tool_calls_n:  Dict[str, int]   = defaultdict(int)
    tool_total_ms: Dict[str, float] = defaultdict(float)
    for e in events:
        if e.get("kind") == "tool_call_end" and "latency_ms" in e:
            a = _agent_of(e)
            tool_calls_n[a]  += 1
            tool_total_ms[a] += e["latency_ms"]

    # SM processing: processing_call_end.latency_ms
    sm_calls_n:   Dict[str, int]   = defaultdict(int)
    sm_total_ms:  Dict[str, float] = defaultdict(float)
    for e in events:
        if e.get("kind") == "processing_call_end" and "latency_ms" in e:
            a = _agent_of(e)
            sm_calls_n[a]  += 1
            sm_total_ms[a] += e["latency_ms"]

    # Inter-agent wait is not isolated in the trace schema yet; keep the column
    # for downstream figures that subtract it (legacy CSV used 0.0).
    comm_wait_ms: Dict[str, float] = defaultdict(float)

    # Governance: use governance_event (has agent_id + hook + outcome)
    gov_n:     Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    gov_fired: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        if e.get("kind") != "governance_event":
            continue
        a    = e.get("agent_id", "")
        hook = e.get("hook", "other")
        if hook not in _GOV_HOOKS:
            hook = "other"
        gov_n[a][hook]     += 1
        if e.get("outcome") == "fired":
            gov_fired[a][hook] += 1

    # Routing: source_agent_id / target_agent_id
    routing_sent: Dict[str, int] = defaultdict(int)
    routing_recv: Dict[str, int] = defaultdict(int)
    for e in events:
        if e.get("kind") == "routing":
            routing_sent[e.get("source_agent_id", "")] += 1
            routing_recv[e.get("target_agent_id", "")]  += 1

    # Context: context_part_contributed per agent
    ctx_parts:  Dict[str, int] = defaultdict(int)
    ctx_tokens: Dict[str, int] = defaultdict(int)
    for e in events:
        if e.get("kind") == "context_part_contributed":
            a = _agent_of(e)
            ctx_parts[a]  += 1
            ctx_tokens[a] += int(e.get("token_estimate") or 0)

    # ── Build rows ────────────────────────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    all_agents_union = sorted(
        set(all_agents)
        | set(llm_calls_n)
        | set(tool_calls_n)
        | set(sm_calls_n)
        | set(gov_n)
        | set(routing_sent)
        | set(routing_recv)
        | set(ctx_parts)
    )

    for agent in all_agents_union:
        n_llm  = llm_calls_n[agent]
        n_tool = tool_calls_n[agent]
        n_sm   = sm_calls_n[agent]

        row: Dict[str, Any] = {
            "scenario":   scenario,
            "item":       item,
            "item_group": item_group,
            "run":        run_idx,
            "agent_id":   agent,
            "execution_ms": round(exec_ms.get(agent, 0.0), 3),
            "llm_calls":    n_llm,
            "llm_total_ms": round(llm_total_ms[agent], 3),
            "llm_mean_ms":  round(llm_total_ms[agent] / n_llm, 3) if n_llm else 0.0,
            "tool_calls":   n_tool,
            "tool_total_ms": round(tool_total_ms[agent], 3),
            "tool_mean_ms":  round(tool_total_ms[agent] / n_tool, 3) if n_tool else 0.0,
            "sm_calls":      n_sm,
            "sm_total_ms":   round(sm_total_ms[agent], 3),
            "agent_comm_wait_ms": round(comm_wait_ms.get(agent, 0.0), 3),
            "routing_sent":     routing_sent[agent],
            "routing_received": routing_recv[agent],
            "context_parts":  ctx_parts[agent],
            "context_tokens": ctx_tokens[agent],
        }

        # Governance columns per hook
        for hook in _GOV_HOOKS:
            col = hook.replace("pre_agent_communication", "pre_agent_comm")
            row[f"gov_{col}_n"]     = gov_n[agent].get(hook, 0)
            row[f"gov_{col}_fired"] = gov_fired[agent].get(hook, 0)
        row["gov_other_n"]     = gov_n[agent].get("other", 0)
        row["gov_other_fired"] = gov_fired[agent].get("other", 0)

        rows.append(row)

    # ── Per-run JSON artifact ─────────────────────────────────────────────────
    if write_per_run_json:
        run_dir = events_path.parent.parent   # <run_dir>/traces/events.jsonl
        json_path = run_dir / "mealy_stats.json"
        try:
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump(
                    {
                        "scenario": scenario,
                        "item":     item,
                        "run":      run_idx,
                        "agents":   rows,
                    },
                    jf,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("Could not write %s: %s", json_path, exc)

    return rows


_FIELDNAMES = [
    "scenario", "item", "item_group", "run", "run_hash", "agent_id",
    "execution_ms",
    "llm_calls", "llm_total_ms", "llm_mean_ms",
    "tool_calls", "tool_total_ms", "tool_mean_ms",
    "sm_calls",   "sm_total_ms", "agent_comm_wait_ms",
    "gov_pre_execution_n",    "gov_pre_execution_fired",
    "gov_pre_llm_call_n",     "gov_pre_llm_call_fired",
    "gov_pre_tool_call_n",    "gov_pre_tool_call_fired",
    "gov_pre_agent_comm_n",   "gov_pre_agent_comm_fired",
    "gov_user_output_n",      "gov_user_output_fired",
    "gov_other_n",            "gov_other_fired",
    "routing_sent", "routing_received",
    "context_parts", "context_tokens",
]


class ExtractMealyStatsStep(PipelineStep):
    """Extract per-run, per-agent Mealy machine statistics from events.jsonl."""

    type = "extract_mealy_stats"

    async def execute(self, ctx: "Any") -> StepOutput:  # noqa: F821
        config = self.config

        output_dir = resolve_step_root(ctx, config, step=self.name)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "mealy_stats.csv", config, default_subdir="results", mkdir=True
        )

        scenario_filter: Optional[List[str]] = config.get("scenarios")
        item_glob: str    = config.get("item_glob", "item*")
        run_glob: str     = config.get("run_glob", "r*")
        write_json: bool  = config.get("write_per_run_json", True)

        all_rows: List[Dict[str, Any]] = []

        for scenario_dir in sorted(output_dir.iterdir()):
            if not scenario_dir.is_dir():
                continue
            scenario = scenario_dir.name
            if scenario_filter and scenario not in scenario_filter:
                continue
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
                        logger.debug("No events.jsonl for %s/%s/%s", scenario, item, run_dir.name)
                        continue

                    # Capture run_hash for unified cache linkage
                    _run_hash = ""
                    run_ref_f = run_dir / ".run_ref"
                    if run_ref_f.exists():
                        try:
                            _run_hash = run_ref_f.read_text(encoding="utf-8").strip()
                        except Exception:
                            logger.debug('suppressed', exc_info=True)

                    rows = _extract_mealy_rows(
                        scenario, item, run_idx, events_path, write_json
                    )
                    for r in rows:
                        r["run_hash"] = _run_hash
                    all_rows.extend(rows)
                    logger.debug(
                        "Extracted %d agent rows for %s/%s/r%d",
                        len(rows), scenario, item, run_idx,
                    )

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

        logger.info(
            "Wrote %d rows to %s", len(all_rows), output_path
        )
        return StepOutput(
            data={"path": str(output_path)},
            files=[output_path],
            metadata={"rows": len(all_rows)},
        )
