#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""data_source — unified DataFrame resolver for pipeline steps.

Steps should use :func:`resolve_dataframe` instead of reading CSV directly.
This decouples data flow from serialization: upstream steps can pass DataFrames
in memory; CSV/JSON/Parquet files are just one possible backend.

Resolution rules (evaluated in order)
--------------------------------------
1. ``@step-name``
       Read from ctx.step_outputs["step-name"].data["df"].
       The key defaults to "df"; override with ``@step-name:my_key``.

2. ``@step-name:field``
       Read the named field from ctx.step_outputs["step-name"].data.
       The field value must be a DataFrame.

3. Any other string
       Treat as a file path.  Supported formats (auto-detected from extension):
         .csv      → pd.read_csv
         .parquet  → pd.read_parquet
         .json     → pd.read_json(orient="records")
       Unrecognised extensions fall back to CSV.

Usage in a step::

    from mas.lab.benchmark.pipeline.lib.data_source import resolve_dataframe

    class MyStep(PipelineStep):
        async def execute(self, ctx) -> StepOutput:
            df = resolve_dataframe(self.config.get("data", ""), ctx)
            # ... process df ...

Writing DataFrames for downstream steps — always set both::

    return StepOutput(
        data={"df": summary_df},          # in-memory (primary)
        files=[output_path],              # serialized copy (optional, for debug)
        metadata={...},
    )

The canonical key for a single output DataFrame is ``"df"``.  When a step
produces multiple named DataFrames, use descriptive keys and document them.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import pandas as pd
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

# Default key looked up in StepOutput.data when using @step-name syntax
_DEFAULT_DF_KEY = "df"


def resolve_dataframe(
    source: str,
    ctx: Optional["ExecutionContext"] = None,
    *,
    df_key: str = _DEFAULT_DF_KEY,
    config: Optional[dict] = None,
    step: Any = "",
) -> "pd.DataFrame":
    """Resolve *source* to a pandas DataFrame.

    Parameters
    ----------
    source  : ``@step-name``, ``@step-name:field``, or a file path string.
              With a *ctx*, a file path must be relative and is read from the
              level folder of the step (see ``artifact_paths``).
    ctx     : ExecutionContext — required for ``@`` references.
    df_key  : Default key to look up when using ``@step-name`` (no field part).
    """
    import pandas as pd

    if not source:
        raise ValueError("resolve_dataframe: source is empty")

    if source.startswith("@"):
        # In-memory reference: @step-name  or  @step-name:field
        ref = source[1:]
        if ":" in ref:
            step_name, field = ref.split(":", 1)
        else:
            step_name, field = ref, df_key

        if ctx is None:
            raise ValueError(
                f"resolve_dataframe: source '{source}' requires an ExecutionContext"
            )
        outputs = ctx.step_outputs.get(step_name)
        if outputs is None:
            raise ValueError(
                f"resolve_dataframe: step '{step_name}' not found in ctx.step_outputs. "
                f"Did you declare it in depends_on?"
            )
        data = outputs.data
        if field not in data:
            available = list(data.keys())
            raise ValueError(
                f"resolve_dataframe: step '{step_name}' has no data field '{field}'. "
                f"Available: {available}"
            )

        value = data[field]

        # If the field holds a DataFrame, return it directly (new pattern)
        import pandas as pd
        if isinstance(value, pd.DataFrame):
            logger.debug(
                "resolve_dataframe: read %d rows from in-memory '%s'", len(value), source
            )
            return value

        # If the field holds a string path, read the file (legacy pattern)
        if isinstance(value, str):
            logger.debug(
                "resolve_dataframe: '%s' resolved to path '%s' — reading file",
                source, value,
            )
            return resolve_dataframe(value, ctx=None)

        # If the field holds a list-of-dicts (CSV artifact loaded from cache),
        # convert it to a DataFrame.  All values from csv.DictReader are strings
        # so we cast numeric columns to float64/int64 explicitly.
        # NOTE: do NOT use convert_dtypes() here — it produces nullable Float64
        # which plotnine treats as discrete (categorical), breaking continuous axes.
        if isinstance(value, list):
            logger.debug(
                "resolve_dataframe: '%s' resolved to list[dict] (%d rows) — converting to DataFrame",
                source, len(value),
            )
            df = pd.DataFrame(value)
            for col in df.columns:
                try:
                    df[col] = pd.to_numeric(df[col])
                except (ValueError, TypeError):
                    logger.debug('suppressed', exc_info=True)
            return df

        raise TypeError(
            f"resolve_dataframe: '{source}' resolved to {type(value).__name__}, "
            "expected DataFrame, file path string, or list-of-dicts"
        )

    # File path
    import pandas as pd
    if ctx is not None:
        from mas.lab.benchmark.pipeline.artifact_paths import (
            resolve_step_input_path,
        )

        path = resolve_step_input_path(ctx, step or "data", {**(config or {}), "data": source}, "data")
    else:
        path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"resolve_dataframe: file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(path)
    elif suffix == ".json":
        df = pd.read_json(path, orient="records")
    else:
        df = pd.read_csv(path)

    logger.debug("resolve_dataframe: read %d rows from file '%s'", len(df), path)
    return df


def write_dataframe(
    df: "pd.DataFrame",
    path: Path,
    *,
    fmt: Optional[str] = None,
) -> Path:
    """Write *df* to *path* using the appropriate serializer.

    The format is inferred from the file extension unless *fmt* is given.
    Supported formats: ``csv``, ``parquet``, ``json``.

    Returns the written path (useful for including in StepOutput.files).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = (fmt or path.suffix.lstrip(".")).lower()

    if suffix == "parquet":
        df.to_parquet(path, index=False)
    elif suffix == "json":
        df.to_json(path, orient="records", indent=2)
    else:
        df.to_csv(path, index=False)

    logger.debug("write_dataframe: wrote %d rows to '%s'", len(df), path)
    return path
