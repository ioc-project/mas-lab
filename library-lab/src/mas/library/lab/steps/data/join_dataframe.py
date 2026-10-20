#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""JoinDataFrameStep — concatenate or merge multiple CSV dataframes.

Operations:
  - ``concat``: vertical stack (like pd.concat)
  - ``merge``: SQL-style join on key columns (like pd.merge)

Configuration
-------------
inputs       list[str]     Paths to input CSVs (supports globs).
operation    str            "concat" (default) or "merge".
on           str | list     Column(s) to merge on (required for merge).
how          str            Merge type: "inner", "left", "outer" (default: "inner").
output       str            Output CSV path.
add_source   bool           Add a ``_source`` column with the input filename (default: false).
filter       dict           Optional row filter: {column: value} or {column: [values]}.
select       list[str]      Optional column selection (keep only these columns).
sort_by      str | list     Optional sort columns.

Example YAML::

    - name: join
      type: join_dataframe
      config:
        inputs:
          - results.csv
          - latency.csv
        operation: merge
        on: [scenario, item_id, run_idx]
        output: combined.csv
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    normalize_relative_value,
    resolve_step_artifact_path,
    resolve_step_input_path,
    resolve_step_root,
)

logger = logging.getLogger(__name__)


class JoinDataFrameStep(PipelineStep):
    """Concatenate or merge multiple CSV dataframes."""

    type = "join_dataframe"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        config = self.config

        # ── Resolve input files ────────────────────────────────────────
        raw_inputs: List[str] = config.get("inputs", [])
        if not raw_inputs:
            raise ValueError(f"JoinDataFrameStep '{self.name}': 'inputs' required")

        root = resolve_step_root(ctx, config, step=self.name)
        input_paths: List[Path] = []
        for pattern in raw_inputs:
            rel = normalize_relative_value(self.name, "inputs", pattern, warn=False)
            if any(c in rel for c in "*?["):
                input_paths.extend(sorted(p for p in root.glob(rel) if p.is_file()))
                continue
            p = resolve_step_input_path(ctx, self.name, {**config, "inputs": rel}, "inputs")
            if p.exists():
                input_paths.append(p)
            else:
                logger.warning("JoinDataFrameStep '%s': input not found: %s", self.name, p)

        if not input_paths:
            raise FileNotFoundError(f"JoinDataFrameStep '{self.name}': no input files found")

        # ── Read dataframes ────────────────────────────────────────────
        add_source = config.get("add_source", False)
        dfs: List[pd.DataFrame] = []
        for ip in input_paths:
            df = pd.read_csv(ip)
            if add_source:
                df["_source"] = ip.stem
            dfs.append(df)
            logger.debug("Read %d rows from %s", len(df), ip)

        # ── Operation ──────────────────────────────────────────────────
        operation = config.get("operation", "concat")

        if operation == "concat":
            result = pd.concat(dfs, ignore_index=True)
        elif operation == "merge":
            on_cols = config.get("on")
            if not on_cols:
                raise ValueError(f"JoinDataFrameStep '{self.name}': 'on' required for merge")
            if isinstance(on_cols, str):
                on_cols = [on_cols]
            how = config.get("how", "inner")
            # Coerce merge keys to string to prevent type mismatches
            for df in dfs:
                for col in on_cols:
                    if col in df.columns:
                        df[col] = df[col].astype(str)
            result = dfs[0]
            for df in dfs[1:]:
                result = result.merge(df, on=on_cols, how=how)
        else:
            raise ValueError(f"JoinDataFrameStep '{self.name}': unknown operation '{operation}'")

        # ── Filter ─────────────────────────────────────────────────────
        filter_spec = config.get("filter", {})
        for col, val in filter_spec.items():
            if isinstance(val, list):
                result = result[result[col].isin(val)]
            else:
                result = result[result[col] == val]

        # ── Select columns ─────────────────────────────────────────────
        select_cols = config.get("select")
        if select_cols:
            result = result[[c for c in select_cols if c in result.columns]]

        # ── Sort ───────────────────────────────────────────────────────
        sort_by = config.get("sort_by")
        if sort_by:
            if isinstance(sort_by, str):
                sort_by = [sort_by]
            result = result.sort_values(sort_by)

        # ── Write output ───────────────────────────────────────────────
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "joined.csv", config, name_keys=(), mkdir=True
        )
        result.to_csv(output_path, index=False)

        logger.info("JoinDataFrameStep '%s': %d rows → %s", self.name, len(result), output_path)
        return StepOutput(
            data={"rows": len(result), "csv_path": str(output_path)},
            files=[output_path],
            metadata={"output": str(output_path), "columns": list(result.columns)},
        )
