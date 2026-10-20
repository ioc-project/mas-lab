#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""DiffTrajectoriesStep — compare trajectory.json files across benchmark runs.

Configuration
-------------
baseline_dir    str   Directory containing baseline run folders (required)
candidate_dir   str   Directory containing candidate run folders (required)
output          str   Relative output path (default: trajectory_diff.json)
match_on        list  Path segments to pair runs (default: [scenario, item_id, run])

Example YAML::

    - name: diff-trajectories
      type: diff_trajectories
      config:
        baseline_dir: output/baseline
        candidate_dir: output/candidate
      depends_on: [extract-trajectories]
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline import ExecutionContext

logger = logging.getLogger(__name__)


def _run_key(run_dir: Path, match_on: List[str]) -> tuple:
    parts: List[str] = []
    for segment in match_on:
        if segment in run_dir.parts:
            idx = run_dir.parts.index(segment)
            if idx + 1 < len(run_dir.parts):
                parts.append(run_dir.parts[idx + 1])
    if not parts:
        parts = [run_dir.name]
    return tuple(parts)


def _collect_trajectories(root: Path) -> Dict[tuple, Path]:
    out: Dict[tuple, Path] = {}
    for path in root.rglob("trajectory.json"):
        key = _run_key(path.parent, ["item", "r"])
        out[key] = path
    return out


def _load_trajectory(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _diff_trajectory(base: dict, cand: dict) -> dict[str, Any]:
    base_events = base.get("events") or base.get("turns") or []
    cand_events = cand.get("events") or cand.get("turns") or []
    return {
        "baseline_event_count": len(base_events),
        "candidate_event_count": len(cand_events),
        "event_count_delta": len(cand_events) - len(base_events),
        "identical": base_events == cand_events,
    }


class DiffTrajectoriesStep(PipelineStep):
    """Compare extracted trajectories between two benchmark output trees."""

    type = "diff_trajectories"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        cfg = self.config
        baseline_dir = Path(cfg.get("baseline_dir", ""))
        candidate_dir = Path(cfg.get("candidate_dir", ""))
        if not baseline_dir.is_dir() or not candidate_dir.is_dir():
            raise ValueError(
                f"DiffTrajectoriesStep '{self.name}': baseline_dir and candidate_dir must exist"
            )

        match_on = list(cfg.get("match_on", ["item", "r"]))
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "trajectory_diff.json", cfg, name_keys=(), mkdir=True
        )

        baseline = _collect_trajectories(baseline_dir)
        candidate = _collect_trajectories(candidate_dir)

        records: List[Dict[str, Any]] = []
        for key, base_path in sorted(baseline.items()):
            cand_path = candidate.get(key)
            if cand_path is None:
                records.append({"key": key, "status": "missing_candidate"})
                continue
            diff = _diff_trajectory(_load_trajectory(base_path), _load_trajectory(cand_path))
            records.append(
                {
                    "key": key,
                    "status": "compared",
                    "baseline": str(base_path),
                    "candidate": str(cand_path),
                    **diff,
                }
            )

        payload = {"pairs": records, "compared": len(records)}
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info(
            "DiffTrajectoriesStep '%s': wrote %d comparisons to %s",
            self.name,
            len(records),
            output_path,
        )
        return StepOutput(data=payload, artifacts=[str(output_path)])
