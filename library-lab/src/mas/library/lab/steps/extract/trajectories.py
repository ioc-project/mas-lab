#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ExtractTrajectoriesStep — pipeline step that reads benchmark run traces
and builds ``trajectories.jsonl`` for downstream metric annotation and
embedding.

Storage layout (under the benchmark output directory)::

    output/benchmark/
      {scenario}/
        {item_id}/
          {run}/
            run_info.json      ← run metadata
            trajectory.json    ← extracted by this step (per-run artefact)
            traces/
              events.jsonl     ← event trace (all agents, all events)
              agents/          ← per-agent event files written by this step
                moderator.jsonl
                ...
      trajectories.jsonl       ← aggregated index (backward-compat, append-only)

``trajectory.json`` is co-located with the run that produced it.  Deleting a
run directory removes its trajectory.  The aggregated ``trajectories.jsonl``
is kept for backward compatibility with embed / annotate_metrics steps but
should not be treated as the primary artefact.

The step is **idempotent**: run_ids already present in ``trajectories.jsonl``
are skipped, so it is safe to re-run after adding more benchmark runs.

Config keys::

    runs_dir: str   relative path to the runs/ subdir  (default: "runs")
    csv_file: str   relative path to the results CSV   (default: "mas_benchmark_*.csv")
    overwrite: bool re-extract even if run_id already present (default: false)
"""

import json
import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_input_path
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


def _write_agent_logs(trace_path: Path) -> dict[str, int]:
    """Split ``events.jsonl`` into per-agent JSONL files.

    Writes ``{trace_path.parent}/agents/{agent_id}.jsonl`` for each agent
    found in the event trace.  Returns a mapping of agent_id → event count.

    The per-agent files are the same events as ``events.jsonl`` — just
    filtered.  They are safe to regenerate deterministically.
    """
    agents_dir = trace_path.parent / "agents"
    agents_dir.mkdir(exist_ok=True)

    from collections import defaultdict
    buckets: dict[str, list[str]] = defaultdict(list)

    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                agent_id = json.loads(line).get("agent_id", "")
            except json.JSONDecodeError:
                agent_id = ""
            buckets[agent_id or "__unknown__"].append(line)

    counts: dict[str, int] = {}
    for agent_id, lines in buckets.items():
        out_path = agents_dir / f"{agent_id}.jsonl"
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        counts[agent_id] = len(lines)
    return counts


class ExtractTrajectoriesStep(PipelineStep):
    """Extract per-run trajectories from ``events.jsonl`` traces.

    Reads the run CSV written by ``run_mas_benchmark`` (columns: run_id,
    scenario, item_id, run, group, target_agents, prompt, trace_path, …),
    calls ``TrajectoryExtractor`` per row, and appends new records to
    ``trajectories.jsonl``.
    """

    type = "extract_trajectories"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.lab.dataset.extractor import TrajectoryExtractor

        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Locate the benchmark output dir (parent of this step's output dir)
        bench_dir = ctx.output_dir
        overwrite = self.config.get("overwrite", False)

        # --- Load the results CSV(s) ---
        import glob
        import csv

        csv_path_fixed = resolve_step_input_path(
            ctx, self.name, self.config, "csv_file", "results.csv"
        )
        if csv_path_fixed.exists():
            csv_matches = [str(csv_path_fixed)]
        else:
            # Legacy fallback: glob timestamped files
            csv_matches = sorted(glob.glob(str(bench_dir / "mas_benchmark_*.csv")))

        # Merge ALL CSV files so every benchmark invocation contributes rows.
        # Later rows for the same run_id overwrite earlier ones (most-recent wins).
        rows_by_id: dict[str, dict] = {}
        for csv_path in csv_matches:
            with open(Path(csv_path), newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    rid = row.get("run_id", "")
                    if rid:
                        rows_by_id[rid] = row

        # Fallback: discover orphaned run dirs that have an events.jsonl but no
        # CSV entry (e.g. the process was killed before the CSV was written).
        runs_base = resolve_step_input_path(ctx, self.name, self.config, "runs_dir", "runs")
        if runs_base.is_dir():
            for run_dir in sorted(runs_base.iterdir()):
                if not run_dir.is_dir():
                    continue
                rid = run_dir.name
                if rid in rows_by_id:
                    continue
                events_file = run_dir / "traces" / "events.jsonl"
                if events_file.exists():
                    # Synthesise a minimal CSV-like row from the run_id name.
                    # Format: {scenario}__item{item_id}__r{run_idx}
                    parts = rid.split("__")
                    scenario_ = parts[0] if len(parts) >= 1 else rid
                    item_id_ = parts[1].lstrip("item") if len(parts) >= 2 else "0"
                    run_ = parts[2].lstrip("r") if len(parts) >= 3 else "1"
                    # Try to extract prompt from the first execution_start event.
                    prompt_ = ""
                    try:
                        with open(events_file, encoding="utf-8") as ef:
                            for line in ef:
                                ev = json.loads(line)
                                if ev.get("kind") == "execution_start":
                                    prompt_ = ev.get("input", "")[:500]
                                    break
                    except Exception as exc:
                        logger.debug("Could not read prompt from %s: %s", events_file, exc)
                    rows_by_id[rid] = {
                        "run_id": rid,
                        "scenario": scenario_,
                        "item_id": item_id_,
                        "run": run_,
                        "group": "",
                        "target_agents": "",
                        "prompt": prompt_,
                        "status": "unknown",
                        "elapsed_ms": "0",
                        "trace_path": str(events_file),
                        "output": "",
                        "error": "",
                    }
                    logger.debug("Discovered orphaned run dir: %s", rid)

        rows = list(rows_by_id.values())
        if csv_matches:
            logger.info("Loaded %d unique runs from %d CSV file(s) + orphan scan", len(rows), len(csv_matches))
        else:
            logger.info("No CSV files found — built %d run entries from orphan scan", len(rows))
            if not rows:
                return StepOutput(metadata={"extracted": 0, "skipped": 0})

        # --- Load already-extracted run_ids (or full records if overwrite) ---
        traj_path = bench_dir / "trajectories.jsonl"
        # When overwrite=True we need to replace stale entries in-place, so we
        # load all existing records keyed by run_id and will rewrite the file.
        existing_records: dict[str, dict] = {}
        existing_ids: set[str] = set()
        if traj_path.exists():
            for line in traj_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rec = json.loads(line)
                        rid = rec.get("run_id", "")
                        if rid:
                            existing_records[rid] = rec
                            if not overwrite:
                                existing_ids.add(rid)
                    except json.JSONDecodeError:
                        logger.debug('suppressed', exc_info=True)

        # --- Extract ---
        extracted = 0
        skipped = 0
        primary_trace_path = ""
        # CSV run_ids that we will (re-)extract
        csv_run_ids: set[str] = {row.get("run_id", "") for row in rows if row.get("run_id")}

        new_records: dict[str, dict] = {}
        for row in rows:
            run_id = row.get("run_id", "")
            if not run_id:
                continue
            # Capture the first available trace path regardless of whether this
            # run still needs extraction, so downstream steps (e.g. the
            # trajectory plot) can resolve a primary run even on a re-run where
            # every run was already extracted and skipped below.
            _row_trace = row.get("trace_path", "")
            if _row_trace and not primary_trace_path:
                primary_trace_path = _row_trace
            if run_id in existing_ids:
                skipped += 1
                continue

            trace_path = row.get("trace_path", "")
            target_agents_raw = row.get("target_agents", "")
            target_agents = (
                [a.strip() for a in target_agents_raw.split(",") if a.strip()]
                if target_agents_raw else []
            )

            run_meta = {
                "run_id": run_id,
                "scenario": row.get("scenario", ""),
                "item_id": row.get("item_id", ""),
                "run": int(row.get("run", 0)),
                "group": row.get("group", ""),
                "target_agents": target_agents,
                "prompt": row.get("prompt", ""),
                "status": row.get("status", ""),
                "elapsed_ms": float(row.get("elapsed_ms", 0) or 0),
            }

            extractor = TrajectoryExtractor(trace_path, run_meta=run_meta)
            record = extractor.extract()
            new_records[run_id] = record

            # Split events.jsonl into per-agent files alongside the event trace.
            if trace_path and Path(trace_path).exists():
                try:
                    agent_counts = _write_agent_logs(Path(trace_path))
                    logger.debug(
                        "  [%s] per-agent logs: %s",
                        run_id,
                        ", ".join(f"{a}={n}" for a, n in sorted(agent_counts.items())),
                    )
                except Exception as _e:
                    logger.warning("Could not write agent logs for %s: %s", run_id, _e)

            extracted += 1

        if overwrite and new_records:
            # Rewrite the file: keep existing records not in this CSV batch,
            # then replace/add the freshly extracted ones.
            merged = {rid: rec for rid, rec in existing_records.items() if rid not in csv_run_ids}
            merged.update(new_records)
            with open(traj_path, "w", encoding="utf-8") as out_f:
                for rec in merged.values():
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        else:
            # Append-only path (overwrite=False or nothing new to write)
            with open(traj_path, "a", encoding="utf-8") as out_f:
                for rec in new_records.values():
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Write trajectory.json into the run directory (co-located with
        # run_info.json and traces/).  This is the canonical per-run artefact.
        # Derive the run dir from trace_path when available (reliable); fall
        # back to reconstructing the path from the run_id when not.
        per_run_files: list[Path] = []
        for rid, rec in new_records.items():
            trace = rows_by_id.get(rid, {}).get("trace_path", "")
            if trace and Path(trace).exists():
                run_dir = Path(trace).parent.parent  # .../traces/events.jsonl → ../
            else:
                # Reconstruct: run_id = "{scenario}__item{item_id}__r{run}"
                parts = rid.split("__")
                if len(parts) == 3:
                    run_dir = bench_dir / parts[0] / parts[1] / parts[2]
                else:
                    run_dir = bench_dir / "unknown" / rid
            run_dir.mkdir(parents=True, exist_ok=True)
            p = run_dir / "trajectory.json"
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            per_run_files.append(p)
            logger.debug("  [%s] trajectory.json → %s", rid, run_dir)

        logger.info(
            "Trajectories: extracted=%d skipped=%d  → %s  + %d trajectory.json files",
            extracted, skipped, traj_path, len(per_run_files),
        )

        return StepOutput(
            data={
                "trajectories_path": str(traj_path),
                "trace_path": primary_trace_path,
                "events_path": primary_trace_path,
            },
            files=[traj_path, *per_run_files],
            metadata={"extracted": extracted, "skipped": skipped},
        )
