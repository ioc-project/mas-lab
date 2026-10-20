#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ToDataFrameStep — convert JSON artifacts into a tidy CSV dataframe.

Scans a directory tree for JSON files matching a pattern, extracts
fields via a declarative column mapping, and writes a CSV.

This is the generic version of ``CollectMetricsStep`` — it works with
any JSON artifacts, not just metrics.json.

Configuration
-------------
input_dir    str           Folder to scan, relative to the level folder (default: ".").
glob         str           Glob pattern for JSON files (default: "**/*.json").
columns      dict          Column mapping: {csv_col: json_path}.
                           JSON paths use dot notation: "session.goal_success_rate.value".
                           Special paths:
                             ``_filename``   — stem of the JSON file
                             ``_parent``     — parent directory name
                             ``_parent.N``   — N-th parent (0=immediate, 1=grandparent, …)
                           Pipe transforms (append after path):
                             ``|re:PATTERN``  — apply regex; use first capture group
                             e.g.  ``_parent.1|re:(\\d+)``  →  "item1" becomes "1"
output       str           Relative output path (default: "data.csv").
flatten      str           If set, flatten a nested dict into rows.
                           E.g. ``flatten: session`` expands session keys into
                           rows with ``_key`` and ``_entry.*`` columns.

Example YAML::

    - name: collect
      type: to_dataframe
      config:
        glob: "*/item*/r*/metrics.json"
        flatten: session
        columns:
          scenario: _parent.2
          item_id: "_parent.1|re:(\\d+)"
          run_idx: "_parent.0|re:(\\d+)"
          metric: _key
          value: _entry.value
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
    step_artifact_relpath,
)
from mas.lab.benchmark.pipeline.resources import Artifact, Scope

logger = logging.getLogger(__name__)


def _parse_column_spec(spec: str) -> tuple[str, str | None]:
    """Split ``path|re:PATTERN`` → (path, pattern) or (path, None)."""
    if "|re:" in spec:
        path, pattern = spec.split("|re:", 1)
        return path, pattern
    return spec, None


def _apply_transform(value: Any, pattern: str | None) -> Any:
    """Apply regex capture-group transform if pattern is set."""
    if pattern is None or value is None:
        return value
    m = re.search(pattern, str(value))
    if m and m.lastindex:
        return m.group(1)
    return value


def _resolve_json_path(doc: Any, path: str) -> Any:
    """Resolve a dot-separated path in a JSON document."""
    parts = path.split(".")
    current = doc
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


class ToDataFrameStep(PipelineStep):
    """Convert JSON artifacts into a tidy CSV dataframe."""

    type = "to_dataframe"

    @property
    def output_artifacts(self) -> List[Tuple[str, Artifact]]:
        rel = step_artifact_relpath(self.name, "data.csv", self.config)
        artifact = Artifact(name=Path(rel).stem, format="csv", scope=Scope.EXPERIMENT)
        artifact._relative_path = rel  # type: ignore[attr-defined]
        return [("csv_path", artifact)]

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        config = self.config

        input_dir = resolve_step_input_path(ctx, self.name, config, "input_dir", ".")
        if input_dir is None or not input_dir.exists():
            raise ValueError(f"ToDataFrameStep '{self.name}': input_dir must exist")

        glob_pattern = config.get("glob", "**/*.json")
        columns: Dict[str, str] = config.get("columns", {})
        flatten_key: Optional[str] = config.get("flatten")
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "data.csv", config, mkdir=True
        )

        json_files = sorted(input_dir.glob(glob_pattern))
        if not json_files:
            logger.warning("ToDataFrameStep '%s': no files matching %s in %s",
                           self.name, glob_pattern, input_dir)
            return StepOutput(data={"rows": 0}, metadata={"error": "no files"})

        rows: list[dict] = []

        for jf in json_files:
            try:
                doc = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipping %s: %s", jf, exc)
                continue

            # Build path-derived context
            rel = jf.relative_to(input_dir)
            # rel = "scenario/item1/r1/metrics.json"
            # parts[:-1] reversed → [r1, item1, scenario]
            rel_parts = list(rel.parts[:-1])  # drop filename
            rel_parts.reverse()               # _parent.0 = immediate parent

            path_ctx = {
                "_filename": jf.stem,
                "_parent": jf.parent.name,
            }
            for i, name in enumerate(rel_parts):
                path_ctx[f"_parent.{i}"] = name

            if flatten_key:
                nested = doc.get(flatten_key, {}) if isinstance(doc, dict) else {}
                if not isinstance(nested, dict):
                    continue
                for key, entry in nested.items():
                    row: dict = {}
                    flat_ctx = {**path_ctx, "_key": key}
                    if isinstance(entry, dict):
                        for ek, ev in entry.items():
                            flat_ctx[f"_entry.{ek}"] = ev
                    else:
                        flat_ctx["_entry"] = entry
                    for col_name, col_spec in columns.items():
                        col_path, col_re = _parse_column_spec(col_spec)
                        if col_path.startswith("_"):
                            row[col_name] = _apply_transform(flat_ctx.get(col_path, ""), col_re)
                        else:
                            row[col_name] = _apply_transform(_resolve_json_path(doc, col_path), col_re)
                    rows.append(row)
            else:
                row = {}
                for col_name, col_spec in columns.items():
                    col_path, col_re = _parse_column_spec(col_spec)
                    if col_path.startswith("_"):
                        row[col_name] = _apply_transform(path_ctx.get(col_path, ""), col_re)
                    else:
                        row[col_name] = _apply_transform(_resolve_json_path(doc, col_path), col_re)
                rows.append(row)

        if not rows:
            return StepOutput(data={"rows": 0}, metadata={"error": "no data"})

        fieldnames = list(columns.keys()) if columns else list(rows[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info("ToDataFrameStep '%s': %d rows → %s", self.name, len(rows), output_path)
        return StepOutput(
            data={"rows": len(rows), "csv_path": str(output_path)},
            files=[output_path],
            metadata={"output": str(output_path)},
        )
