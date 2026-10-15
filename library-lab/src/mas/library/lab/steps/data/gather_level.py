#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""GatherLevelStep — concatenate child dataframe artifacts at this folder."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput

logger = logging.getLogger(__name__)


def _filter_identity(df: Any, config: Dict[str, Any]) -> Any:
    """Keep rows for this instance when a batched parent leaked the full frame."""
    if df is None or getattr(df, "empty", True):
        return df
    scenario = str(config.get("scenario") or "")
    test = str(config.get("test") or "")
    run = str(config.get("run") or "")
    out = df
    if scenario:
        for col in ("scenario", "scenario_id"):
            if col in out.columns:
                out = out[out[col].astype(str) == scenario]
                break
    if test:
        item_id = test[4:] if test.startswith("item") else test
        for col, want in (("test", test), ("item", test), ("item_id", item_id)):
            if col in out.columns:
                out = out[out[col].astype(str) == str(want)]
                break
    if run:
        run_idx = run[1:] if run.startswith("r") else run
        for col, want in (("run", run), ("run_id", run), ("run_idx", run_idx)):
            if col in out.columns:
                out = out[out[col].astype(str) == str(want)]
                break
    return out.reset_index(drop=True) if out is not df else out


def _frames_from_deps(step: "GatherLevelStep", ctx: Any) -> List[Any]:
    frames: List[Any] = []
    for dep_name in step.depends_on or []:
        dep_out = ctx.step_outputs.get(dep_name)
        if dep_out is None:
            continue
        data = dep_out.data if isinstance(dep_out.data, dict) else {}
        df = data.get("df")
        if df is None or getattr(df, "empty", True):
            continue
        frames.append(df)
    return frames


class GatherLevelStep(PipelineStep):
    """Concat CSV/parquet files listed in ``config["artifact_paths"]``."""

    type = "gather_level"

    async def execute(self, ctx: Any) -> StepOutput:
        import pandas as pd

        config = self.config
        annotate: Dict[str, Any] = config.get("annotate") or {}
        fmt: str = config.get("format", "csv")

        frames = _frames_from_deps(self, ctx)
        paths = [Path(str(p)) for p in (config.get("artifact_paths") or [])]
        if not frames:
            for path in paths:
                if not path.exists():
                    logger.warning("gather_level '%s': missing %s", self.name, path)
                    continue
                df_part = (
                    pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
                )
                if df_part is None or df_part.empty:
                    continue
                frames.append(df_part)

        if not frames:
            if paths:
                detail = (
                    f"none of {len(paths)} configured artifact_paths exist or "
                    f"contain data (checked {paths[0]}{', ...' if len(paths) > 1 else ''})"
                )
            else:
                detail = (
                    'config["artifact_paths"] was empty — no child folders were '
                    "found for this level, or the `in:` artifact name didn't "
                    "resolve to anything the level below declares"
                )
            raise RuntimeError(
                f"gather_level '{self.name}': {detail} — fan-in produced nothing to concatenate."
            )

        from mas.lab.benchmark.pipeline.gather import gather_records

        df = pd.concat(frames, ignore_index=True)
        df = _filter_identity(df, config)
        if annotate:
            rows = gather_records([df.to_dict(orient="records")], annotate=annotate)
            df = pd.DataFrame(rows) if rows else df.iloc[0:0]

        output_dir = Path(config["output_dir"]) if config.get("output_dir") else ctx.output_dir
        output_path = Path(config.get("output", f"data.{fmt}"))
        if not output_path.is_absolute():
            output_path = output_dir / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "parquet":
            df.to_parquet(output_path, index=False)
        else:
            df.to_csv(output_path, index=False)

        return StepOutput(
            data={"df": df, "df_path": str(output_path), "rows": len(df)},
            files=[output_path],
            metadata={"rows": len(df), "parts": len(frames)},
        )
