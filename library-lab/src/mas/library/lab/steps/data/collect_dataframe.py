#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""CollectDataFrameStep — gather per-level DataFrames into an experiment-level tidy CSV.

This step is a **gather operator** that collects DataFrames produced by
lower-level pipeline steps and concatenates them into a single tidy
experiment-level DataFrame.

Two modes of operation
----------------------

**Stream mode** (primary — pipeline execution):
    When ``depends_on`` lists upstream steps, the execution engine has
    already fan-in'd each per-run / per-scenario step instance into
    ``ctx.step_outputs``.  Each dependency is expected to expose a
    DataFrame under ``data["df"]``.  Legacy dependencies that only expose
    ``data["csv_path"]`` are also supported.

    Example (v2 experiment YAML)::

        run:
          pipeline:
            - name: extract-stats
              type: extract_trace_stats   # produces data["df"] per run

        experiment:
          pipeline:
            - name: collect-dataframe
              type: collect_dataframe
              depends_on: [extract-stats]   # runner expands → extract-stats-<scenario>
              config:
                output: results.csv

**Filesystem fallback** (notebook / offline use):
    When ``depends_on`` is empty (standalone use or direct notebook call),
    traverses the benchmark output tree via
    :class:`~mas.lab.benchmark.results.ExperimentResults` to read
    ``filename`` from every ``scenario/item*/r*/`` directory.

    Example (standalone YAML step)::

        - name: collect-dataframe
          type: collect_dataframe
          config:
            filename: metrics.json
            flatten: session
            output: results.csv

Resume via artifacts
--------------------
The step declares ``output_artifacts`` so that when a previous run is
restored from cache the executor re-populates ``ctx.step_outputs`` without
re-executing.

Downstream reference::

    - name: compute-ci
      type: compute_ci
      config:
        data: "@collect-dataframe"
        groupby: [scenario, metric]
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_root,
    step_artifact_relpath,
)
from mas.lab.benchmark.pipeline.resources import Artifact, Scope
from mas.lab.benchmark.results import ExperimentResults

logger = logging.getLogger(__name__)


def _df_from_step_output(dep_name: str, out: "StepOutput") -> "Optional[pd.DataFrame]":
    """Extract a DataFrame from a step output, trying several conventions."""
    import pandas as pd

    data = out.data if isinstance(out.data, dict) else {}

    # 1. Canonical in-memory key (new pattern: data["df"])
    val = data.get("df")
    if isinstance(val, pd.DataFrame):
        return val

    # 2. The whole data IS a DataFrame (defensive — should not happen with typed StepOutput)
    if isinstance(out.data, pd.DataFrame):
        return out.data

    # 3. Legacy: data["csv_path"] written by older steps
    csv_raw = data.get("csv_path")
    if csv_raw:
        path = Path(str(csv_raw))
        if path.exists():
            try:
                return pd.read_csv(path)
            except Exception as exc:
                logger.warning("collect_dataframe: cannot read csv from dep '%s': %s", dep_name, exc)

    return None


class CollectDataFrameStep(PipelineStep):
    """Gather per-level DataFrames from upstream steps into an experiment-level tidy CSV.

    See module docstring for full documentation of stream vs fallback modes.
    """

    type = "collect_dataframe"

    @property
    def output_artifacts(self) -> List[Tuple[str, Artifact]]:
        rel = step_artifact_relpath(self.name, "results.csv", self.config, name_keys=())
        artifact = Artifact(name=Path(rel).stem, format="csv", scope=Scope.EXPERIMENT)
        artifact._relative_path = rel  # type: ignore[attr-defined]
        return [("csv_path", artifact)]

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        import pandas as pd

        config = self.config
        filename: str = config.get("filename", "metrics.json")
        flatten: Optional[str] = config.get("flatten")

        output_dir = resolve_step_root(ctx, config, step=self.name)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "results.csv", config, name_keys=(), mkdir=True
        )

        # ── Mode 1: stream — fan-in from upstream step outputs ───────────────
        # When depends_on is set the runner has already expanded each per-run /
        # per-scenario step into individual instances (e.g. "eval-baseline",
        # "eval-challenge", …) and all their StepOutputs are in ctx.step_outputs.
        frames: List["pd.DataFrame"] = []

        if self.depends_on:
            for dep_name in self.depends_on:
                dep_out = ctx.step_outputs.get(dep_name)
                if dep_out is None:
                    logger.warning(
                        "collect_dataframe: dep '%s' not in step_outputs (skipped)", dep_name
                    )
                    continue
                df_part = _df_from_step_output(dep_name, dep_out)
                if df_part is not None and not df_part.empty:
                    frames.append(df_part)
                else:
                    logger.debug(
                        "collect_dataframe: dep '%s' produced no DataFrame (skipped)", dep_name
                    )

            if frames:
                logger.info(
                    "collect_dataframe: gathered %d DataFrames from %d upstream steps",
                    len(frames),
                    len(self.depends_on),
                )
            else:
                logger.warning(
                    "collect_dataframe: no DataFrames found in %d upstream steps; "
                    "falling back to filesystem traversal",
                    len(self.depends_on),
                )

        # ── Mode 2: filesystem fallback — ExperimentResults traversal ────────
        # Used when: (a) depends_on is empty (standalone / notebook / v1 YAML),
        #            (b) upstream steps provided no DataFrames (all skipped above).
        if not frames:
            exp = ExperimentResults.from_output_dir(output_dir)
            logger.info(
                "collect_dataframe: filesystem mode — reading %s from %d scenarios in %s",
                filename,
                len(exp.scenarios),
                output_dir,
            )
            df = exp.collect_dataframe(filename, flatten=flatten)
        else:
            df = pd.concat(frames, ignore_index=True)

        # ── Persist artifact ─────────────────────────────────────────────────
        if df.empty:
            logger.warning("collect_dataframe: no data found in %s", output_dir)
        else:
            df.to_csv(output_path, index=False)
            logger.info(
                "collect_dataframe: wrote %d rows × %d cols to %s",
                len(df), len(df.columns), output_path,
            )

        return StepOutput(
            data={"df": df, "csv_path": str(output_path)},
            files=[output_path] if output_path.exists() else [],
            metadata={"rows": len(df), "cols": len(df.columns), "source": str(output_dir)},
        )
