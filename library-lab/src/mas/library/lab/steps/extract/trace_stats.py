#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ExtractTraceStatsStep — extract per-run governance and trajectory metrics
from events.jsonl trace files.

Scans ``<output_dir>/<scenario>/<item>/r<N>/traces/events.jsonl`` for every
scenario/item/run triple, computes a set of structural metrics from the raw
event stream, and writes a tidy CSV.

No LLM judge is invoked; all metrics are derived directly from trace events.

Output columns
--------------
scenario          str    Scenario folder name.
item              str    Item folder name (e.g. itemn1, itemf-budget).
item_group        str    Derived from item prefix: nominal / fault /
                         guardrail / recall / combination / other.
run               int    Run index (extracted from r<N> folder name).
duration_s        float  Wall-clock duration: max(timestamp) - min(timestamp).
n_governance_checks  int  Count of ``governance_checked`` events.
n_governance_fired   int  Count of ``governance_policy`` events where
                          ``outcome == "fired"``.
n_checks_passed      int  Sum of ``checks_passed`` across all
                          ``governance_event`` records — total number
                          of policy evaluations (scales with policy count).
n_tool_calls      int    Count of ``tool_call_start`` events.
n_llm_calls       int    Count of ``llm_call_start`` events.
prompt_tokens     int    Sum of ``tokens_used.prompt_tokens`` on ``llm_call_end``.
completion_tokens int    Sum of ``tokens_used.completion_tokens`` on ``llm_call_end``.
total_tokens      int    Sum of ``tokens_used.total_tokens`` on ``llm_call_end``.
cached_tokens     int    Sum of cached/read prompt tokens on ``llm_call_end``.
n_context_tokens  int    Sum of ``token_estimate`` across
                         ``context_part_contributed`` events.
n_context_parts   int    Count of ``context_part_contributed`` events.

Configuration
-------------
output_dir   str   Root directory to scan. Defaults to ``ctx.output_dir``.
output       str   Output CSV path (default: ``trace_stats.csv`` inside
                   ``<output_dir>/results/``).
scenarios    list  Optional list of scenario IDs to include.
item_glob    str   Glob pattern for item directories (default: ``item*``).
run_glob     str   Glob pattern for run directories (default: ``r*``).

Example YAML::

    - name: extract-trace-stats
      type: extract_trace_stats
      config:
        output: "{output_dir}/results/trace_stats.csv"
"""

import csv
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.cache.trace_store import resolve_run_events_path as _resolve_events_path
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.schema.validation import check_trace_integrity
from mas.library.lab.steps.services.metric_event import publish_metric_record

logger = logging.getLogger(__name__)

# Map item name prefix → group label
_GROUP_MAP = [
    ("itemn", "nominal"),
    ("itemf", "fault"),
    ("itemr", "recall"),
    ("itemc", "combination"),
    ("itemg", "guardrail"),
]


def _item_group(item: str) -> str:
    for prefix, group in _GROUP_MAP:
        if item.startswith(prefix):
            return group
    return "other"


def _run_index(run_dir: str) -> int:
    m = re.search(r"(\d+)", run_dir)
    return int(m.group(1)) if m else 0


_TRACE_METRIC_KEYS = (
    "duration_s",
    "n_governance_checks",
    "n_governance_fired",
    "n_checks_passed",
    "n_tool_calls",
    "n_llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "n_context_parts",
    "n_context_tokens",
    "n_selfref_events",
)


def empty_trace_stats() -> Dict[str, Any]:
    return {
        "duration_s": 0.0,
        "n_governance_checks": 0,
        "n_governance_fired": 0,
        "n_checks_passed": 0,
        "n_tool_calls": 0,
        "n_llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "n_context_parts": 0,
        "n_context_tokens": 0,
        "n_selfref_events": 0,
        "_timestamps": [],
    }


def public_trace_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    timestamps = stats.get("_timestamps") or []
    duration_s = (max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0.0
    return {
        "duration_s": round(duration_s, 3),
        "n_governance_checks": int(stats.get("n_governance_checks") or 0),
        "n_governance_fired": int(stats.get("n_governance_fired") or 0),
        "n_checks_passed": int(stats.get("n_checks_passed") or 0),
        "n_tool_calls": int(stats.get("n_tool_calls") or 0),
        "n_llm_calls": int(stats.get("n_llm_calls") or 0),
        "prompt_tokens": int(stats.get("prompt_tokens") or 0),
        "completion_tokens": int(stats.get("completion_tokens") or 0),
        "total_tokens": int(stats.get("total_tokens") or 0),
        "cached_tokens": int(stats.get("cached_tokens") or 0),
        "n_context_parts": int(stats.get("n_context_parts") or 0),
        "n_context_tokens": int(stats.get("n_context_tokens") or 0),
        "n_selfref_events": int(stats.get("n_selfref_events") or 0),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fold_usage(stats: Dict[str, Any], event: Dict[str, Any]) -> None:
    usage = event.get("tokens_used") or event.get("usage")
    if not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
    prompt = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _as_int(usage.get("total_tokens")) or (prompt + completion)
    cached = _as_int(
        usage.get("cached_tokens")
        or usage.get("cache_read_input_tokens")
        or details.get("cached_tokens")
        or details.get("cache_read_tokens")
    )
    stats["prompt_tokens"] = int(stats.get("prompt_tokens") or 0) + prompt
    stats["completion_tokens"] = int(stats.get("completion_tokens") or 0) + completion
    stats["total_tokens"] = int(stats.get("total_tokens") or 0) + total
    stats["cached_tokens"] = int(stats.get("cached_tokens") or 0) + cached


def fold_trace_event(stats: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Increment structural counters from one native projected record."""
    kind = str(event.get("kind") or "")
    if kind in {"metric", "trace_stats"}:
        return
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        stats.setdefault("_timestamps", []).append(ts)
    if kind == "governance_checked":
        stats["n_governance_checks"] = int(stats.get("n_governance_checks") or 0) + 1
    if kind == "governance_policy" and event.get("outcome") == "fired":
        stats["n_governance_fired"] = int(stats.get("n_governance_fired") or 0) + 1
    if kind == "governance_event":
        stats["n_checks_passed"] = int(stats.get("n_checks_passed") or 0) + int(
            event.get("checks_passed") or 0
        )
    if kind == "tool_call_start":
        stats["n_tool_calls"] = int(stats.get("n_tool_calls") or 0) + 1
    if kind == "llm_call_start":
        stats["n_llm_calls"] = int(stats.get("n_llm_calls") or 0) + 1
        stats["_llm_starts"] = int(stats.get("_llm_starts") or 0) + 1
    if kind == "llm_call_end":
        if not stats.get("_llm_starts"):
            stats["n_llm_calls"] = int(stats.get("n_llm_calls") or 0) + 1
        _fold_usage(stats, event)
    if kind == "context_part_contributed":
        stats["n_context_parts"] = int(stats.get("n_context_parts") or 0) + 1
        stats["n_context_tokens"] = int(stats.get("n_context_tokens") or 0) + int(
            event.get("token_estimate") or 0
        )


def _iter_jsonl_batches(events_path: Path, batch_size: int = 256) -> list:
    """Yield batches of dict records from a jsonl file."""
    try:
        from mas.library.standard.lib.jsonl_iter import iter_jsonl_records

        return list(iter_jsonl_records(events_path, batch_size=batch_size))
    except Exception:
        logger.debug("jsonl_iter unavailable; reading %s linewise", events_path, exc_info=True)
    batches: list = []
    batch: list = []
    try:
        with open(events_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    continue
                batch.append(rec)
                if len(batch) >= batch_size:
                    batches.append(batch)
                    batch = []
    except Exception as exc:
        logger.warning("Failed to read %s: %s", events_path, exc)
        return []
    if batch:
        batches.append(batch)
    return batches


def _extract_stats(events_path: Path) -> Dict[str, Any]:
    """Read one events.jsonl file and return a stats dict."""
    stats = empty_trace_stats()
    events: list = []
    for batch in _iter_jsonl_batches(events_path):
        events.extend(batch)
        for event in batch:
            fold_trace_event(stats, event)
    if not events:
        return {}

    n_selfref = len(check_trace_integrity(events, source=events_path.name))
    if n_selfref:
        logger.warning(
            "%s: %d self-referential start event(s) detected (call_id == parent_call_id)",
            events_path,
            n_selfref,
        )
    stats["n_selfref_events"] = n_selfref
    return public_trace_stats(stats)


class ExtractTraceStatsStep(PipelineStep):
    """Extract per-run structural metrics from events.jsonl trace files.

    ``on_batch`` folds the live or sealed channel (or jsonl batches on disk)
    into the same counters the CSV persist writes. Per-run fold state is
    keyed so overlapping scenarios never mix.
    """

    type = "extract_trace_stats"
    VERSION = "1"
    resource = "cpu"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._live_by_run: Dict[str, Dict[str, Any]] = {}
        self._last_pub_by_run: Dict[str, float] = {}
        self._live = empty_trace_stats()
        self._last_pub = 0.0
        self._active: str = ""

    def _slot(self, event: Dict[str, Any], ctx: "Any") -> str:  # noqa: F821
        if self._active:
            return self._active
        run_id = event.get("run_id")
        if run_id:
            return str(run_id)
        session_id = event.get("session_id")
        if session_id:
            return str(session_id)
        scenario = str(event.get("scenario") or "")
        test = str(event.get("test") or "")
        if scenario or test:
            return f"{scenario}/{test}"
        return "_"

    def begin(self, key: Any, ctx: "Any") -> None:  # noqa: F821
        del ctx
        sid = key.stream_id() if hasattr(key, "stream_id") else str(key)
        self._active = sid
        self._live_by_run[sid] = empty_trace_stats()
        self._last_pub_by_run[sid] = 0.0

    def on_batch(self, batch: list, ctx: "Any") -> None:  # noqa: F821
        for rec in batch:
            if isinstance(rec, dict):
                self._fold_one(rec, ctx)

    def snapshot(self) -> bytes:
        return json.dumps(
            {"live": self._live_by_run, "pub": self._last_pub_by_run, "active": self._active},
            default=str,
        ).encode("utf-8")

    def restore(self, blob: bytes) -> None:
        data = json.loads(blob.decode("utf-8"))
        self._live_by_run = data.get("live") or {}
        self._last_pub_by_run = {k: float(v) for k, v in (data.get("pub") or {}).items()}
        self._active = str(data.get("active") or "")

    def _fold_one(self, event: Dict[str, Any], ctx: "Any") -> None:  # noqa: F821
        slot = self._slot(event, ctx)
        stats = self._live_by_run.setdefault(slot, empty_trace_stats())
        self._live = stats
        fold_trace_event(stats, event)
        kind = str(event.get("kind") or "")
        now = time.monotonic()
        last = self._last_pub_by_run.get(slot, 0.0)
        if now - last < 1.0 and kind not in {"mas_call_end", "execution_end"}:
            return
        self._last_pub_by_run[slot] = now
        self._last_pub = now
        run_id = event.get("run_id")
        if not run_id:
            try:
                run_id = ctx.run_id
            except AttributeError:
                run_id = None
        publish_metric_record(
            "trace_stats",
            public_trace_stats(stats),
            agent_id=event.get("agent_id"),
            run_id=run_id,
            session_id=event.get("session_id"),
            scenario=event.get("scenario"),
        )

    async def execute(self, ctx: "Any") -> StepOutput:  # noqa: F821
        self._ctx = ctx
        config = self.config

        output_dir_raw = config.get("output_dir", "")
        output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else ctx.output_dir

        output_raw = config.get("output", "")
        if output_raw:
            output_path = Path(output_raw).expanduser()
            if not output_path.is_absolute():
                output_path = output_dir / output_path
        else:
            output_path = output_dir / "results" / "trace_stats.csv"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        scenario_filter: Optional[List[str]] = config.get("scenarios")
        item_glob: str = config.get("item_glob", "item*")
        run_glob: str = config.get("run_glob", "r*")

        fieldnames = [
            "scenario", "item", "item_group", "run",
            "run_hash",
            *_TRACE_METRIC_KEYS,
        ]

        rows: List[Dict[str, Any]] = []

        # Discover scenarios
        scenario_dirs = sorted(
            d for d in output_dir.iterdir()
            if d.is_dir()
            and not d.name.startswith(".")
            and d.name not in ("results", "data", "logs", "metadata.yaml")
            and (scenario_filter is None or d.name in scenario_filter)
        )

        for scenario_dir in scenario_dirs:
            scenario = scenario_dir.name
            item_dirs = sorted(scenario_dir.glob(item_glob))
            for item_dir in item_dirs:
                if not item_dir.is_dir():
                    continue
                item = item_dir.name
                run_dirs = sorted(item_dir.glob(run_glob))
                for run_dir in run_dirs:
                    if not run_dir.is_dir():
                        continue
                    events_path = _resolve_events_path(run_dir)
                    if events_path is None:
                        logger.debug("No events.jsonl for run_dir %s", run_dir)
                        continue
                    # Capture run_hash for unified cache linkage
                    _run_hash = ""
                    run_ref_f = run_dir / ".run_ref"
                    if run_ref_f.exists():
                        try:
                            _run_hash = run_ref_f.read_text(encoding="utf-8").strip()
                        except Exception:
                            logger.debug('suppressed', exc_info=True)
                    stats = _extract_stats(events_path)
                    if not stats:
                        continue
                    rows.append({
                        "scenario": scenario,
                        "item": item,
                        "item_group": _item_group(item),
                        "run": _run_index(run_dir.name),
                        "run_hash": _run_hash,
                        **stats,
                    })
                    publish_metric_record(
                        "trace_stats",
                        {key: stats[key] for key in _TRACE_METRIC_KEYS if key in stats},
                        scenario=scenario,
                        test=item,
                        run=_run_index(run_dir.name),
                    )

        # Write CSV
        with open(output_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Wrote %d trace-stats rows to %s", len(rows), output_path)

        # Also expose as in-memory DataFrame (pandas) so downstream steps can
        # use @extract-trace-stats instead of a file path reference.
        try:
            import pandas as pd
            df = pd.DataFrame(rows, columns=fieldnames)
        except ImportError:
            df = None

        data: dict = {"df_path": str(output_path), "rows": len(rows)}
        if df is not None:
            data["df"] = df

        return StepOutput(
            data=data,
            files=[output_path],
            metadata={"scenarios": len(scenario_dirs), "rows": len(rows)},
        )
