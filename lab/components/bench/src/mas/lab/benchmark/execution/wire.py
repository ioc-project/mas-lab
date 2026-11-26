#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""File forms of the two things the lab roles exchange: a run plan and a results tree.

A *plan* (``mas-lab/run-plan/v1``) says which runs to run. A *results tree* is
``<scenario dir>/item<id>/r<n>/result.json`` plus the run's traces. Nothing else
couples generation, scheduling, execution, retry and analysis, so each can run in
its own process or CI job: plan once, run shards on separate runners, merge their
trees, then retry or analyze.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.lab.benchmark.execution.completeness import child_is_complete, read_result, result_is_complete
from mas.lab.benchmark.execution.roles import MAX_SPENT_ATTEMPTS, AttemptRecord, PlannedRun, Retrier

logger = logging.getLogger(__name__)

PLAN_SCHEMA = "mas-lab/run-plan/v1"
DEFAULT_MAX_SHARDS = 256
"""GitHub Actions refuses a matrix of more than 256 jobs."""
SHARD_MODES = ("run", "scenario", "item", "chunk")


def _spent(raw: Any, *, default: int = 0) -> int:
    """An attempt count read from a file: an int in ``[0, MAX_SPENT_ATTEMPTS]``, else ``default``."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return max(0, min(int(raw), MAX_SPENT_ATTEMPTS))


def run_run_dir(root: Path, run: PlannedRun, dir_names: Mapping[str, str] | None = None) -> Path:
    """Where a run's run folder lives under a results tree."""
    scenario_dir = (dir_names or {}).get(run.scenario_id, run.scenario_id)
    return Path(root) / scenario_dir / f"item{run.item_id}" / f"r{run.run_idx + 1}"


def make_shards(
    runs: Sequence[PlannedRun],
    *,
    by: str = "run",
    size: int = 1,
    max_shards: int = DEFAULT_MAX_SHARDS,
) -> list[list[PlannedRun]]:
    """Group runs into shards, order kept. More groups than ``max_shards`` are dealt round-robin."""
    if by not in SHARD_MODES:
        raise ValueError(f"shard mode must be one of {list(SHARD_MODES)}, got {by!r}")
    if size < 1 or max_shards < 1:
        raise ValueError("shard size and max_shards must be >= 1")
    groups: dict[Any, list[PlannedRun]] = {}
    if by == "chunk":
        for index, run in enumerate(runs):
            groups.setdefault(index // size, []).append(run)
    else:
        for run in runs:
            key = {"run": run.id, "scenario": run.scenario_id, "item": str(run.item_id)}[by]
            groups.setdefault(key, []).append(run)
    ordered = list(groups.values())
    if len(ordered) <= max_shards:
        return ordered
    dealt: list[list[PlannedRun]] = [[] for _ in range(max_shards)]
    for index, group in enumerate(ordered):
        dealt[index % max_shards].extend(group)
    return dealt


def plan_document(
    runs: Sequence[PlannedRun],
    *,
    experiment: str,
    experiment_yaml: str,
    generator: str,
    scheduler: str,
    attempts_before: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    before = attempts_before or {}
    return {
        "schema": PLAN_SCHEMA,
        "experiment": experiment,
        "experiment_yaml": experiment_yaml,
        "generator": generator,
        "scheduler": scheduler,
        "runs": [
            {
                "id": run.id,
                "scenario": run.scenario_id,
                "item_id": run.item_id,
                "run": run.run_idx,
                "attempts_before": before.get(run.id, 0),
            }
            for run in runs
        ],
    }


def write_plan(path: Path, document: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def read_plan(path: Path) -> dict[str, Any]:
    """Load and check a plan file. Unknown schema or malformed runs fail loudly."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"{path}: not a {PLAN_SCHEMA} document")
    runs = data.get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"{path}: runs must be a list")
    seen: set[str] = set()
    for row in runs:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"{path}: every run needs a string id")
        if row["id"] in seen:
            raise ValueError(f"{path}: duplicate run id {row['id']!r}")
        seen.add(row["id"])
    return data


def select_runs(runs: Sequence[PlannedRun], plan: Mapping[str, Any]) -> tuple[list[PlannedRun], dict[str, int]]:
    """Keep the generated runs a plan file names, in the generator's order.

    A run the plan names that this experiment does not generate is an error: the plan
    was made for a different experiment or dataset.
    """
    wanted = {row["id"]: _spent(row.get("attempts_before")) for row in plan["runs"]}
    chosen = [run for run in runs if run.id in wanted]
    missing = sorted(set(wanted) - {run.id for run in chosen})
    if missing:
        raise ValueError(
            f"plan names {len(missing)} run(s) this experiment does not generate "
            f"(first: {missing[0]!r}); regenerate the plan from the same experiment and dataset"
        )
    return chosen, {run.id: wanted[run.id] for run in chosen}


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    """``ok``, ``cached``, ``error`` or ``missing`` (no result was written)."""
    error: str = ""
    attempts: int = 0

    @property
    def complete(self) -> bool:
        return child_is_complete(self.status)


def read_results(
    root: Path,
    runs: Sequence[PlannedRun],
    dir_names: Mapping[str, str] | None = None,
) -> dict[str, RunResult]:
    out: dict[str, RunResult] = {}
    for run in runs:
        ident = run.id
        data = read_result(run_run_dir(root, run, dir_names))
        if data is None:
            out[ident] = RunResult(ident, "missing")
            continue
        out[ident] = RunResult(
            ident,
            str(data.get("status") or "error"),
            str(data.get("error") or ""),
            _spent(data.get("attempts"), default=1),
        )
    return out


def runs_to_retry(
    runs: Sequence[PlannedRun],
    results: Mapping[str, RunResult],
    retrier: Retrier,
) -> tuple[list[PlannedRun], dict[str, int], list[str]]:
    """Runs another round should run, their spent attempts, and the ids the retrier gave up on.

    A run with no result (its job was lost) always runs again: nothing ran, so no
    attempt was spent.
    """
    again: list[PlannedRun] = []
    spent: dict[str, int] = {}
    gave_up: list[str] = []
    for run in runs:
        ident = run.id
        result = results.get(ident) or RunResult(ident, "missing")
        if result.complete:
            continue
        if result.status == "missing":
            again.append(run)
            spent[ident] = 0
            continue
        history = [AttemptRecord("error", result.error)] * max(1, min(result.attempts, MAX_SPENT_ATTEMPTS))
        if retrier.decide(history).retry:
            again.append(run)
            spent[ident] = result.attempts
        else:
            gave_up.append(ident)
    return again, spent, gave_up


def _safe_links(source: Path):
    """``copytree`` ignore hook: skip a symlink that points outside ``source`` and the trace cache.

    A trace-cache link is followed so the merged tree stands alone; any other link in a
    tree that came from elsewhere could name a file the merge should not read.
    """
    from mas.lab.paths import trace_cache

    roots = [Path(source).resolve(), Path(trace_cache()).resolve()]

    def ignore(directory: str, names: list[str]) -> list[str]:
        skipped = []
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            target = path.resolve()
            if not any(target == root or root in target.parents for root in roots):
                logger.warning(
                    "merge: skipped link %s -> %s (outside the results tree and the trace cache)", path, target
                )
                skipped.append(name)
        return skipped

    return ignore


def merge_results(sources: Sequence[Path], dest: Path) -> dict[str, int]:
    """Merge shard results trees into ``dest``.

    A run folder that finished ``ok`` is never replaced by one that did not. Between
    two folders of the same standing the later source wins. A link into the trace cache
    is followed so the merged tree stands alone; links elsewhere are skipped.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied = replaced = kept = 0
    for source in sources:
        source = Path(source)
        if not source.is_dir():
            raise FileNotFoundError(f"results source not found: {source}")
        for scenario_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            for item_dir in sorted(p for p in scenario_dir.iterdir() if p.is_dir() and p.name.startswith("item")):
                for run_dir in sorted(p for p in item_dir.iterdir() if p.is_dir() and p.name.startswith("r")):
                    target = dest / scenario_dir.name / item_dir.name / run_dir.name
                    incoming_ok = result_is_complete(read_result(run_dir))
                    if target.exists():
                        if result_is_complete(read_result(target)) and not incoming_ok:
                            kept += 1
                            continue
                        shutil.rmtree(target)
                        replaced += 1
                    else:
                        copied += 1
                    shutil.copytree(
                        run_dir, target, symlinks=False, ignore=_safe_links(source), ignore_dangling_symlinks=True
                    )
    return {"copied": copied, "replaced": replaced, "kept": kept}
