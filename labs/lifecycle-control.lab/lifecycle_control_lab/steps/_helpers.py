#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared helpers for lifecycle-control lab pipeline figure steps."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from mas.lab.benchmark.pipeline import StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_input_path,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASELINE = "baseline/itemf-guardrail/r1/traces/events.jsonl"
_DEFAULT_GUARDRAIL = "with-guardrail/itemf-guardrail/r1/traces/events.jsonl"


def resolve_trace_path(path: Path) -> Path:
    """Follow ``.run_ref`` when a run's ``events.jsonl`` symlink is stale."""
    if path.is_file():
        return path
    if path.name == "events.jsonl" and path.parent.name == "traces":
        from mas.lab.benchmark.cache.trace_store import resolve_run_events_path

        resolved = resolve_run_events_path(path.parent.parent)
        if resolved is not None:
            return resolved
    return path


def _figure_output(ctx: Any, step: Any, default_name: str) -> Path:
    return resolve_step_artifact_path(
        ctx,
        None,
        step.name,
        default_name,
        step.config,
        default_subdir="results",
        mkdir=True,
    )


def _required_input(ctx: Any, step: Any, key: str, default: str) -> Path:
    path = resolve_step_input_path(ctx, step.name, step.config, key, default)
    if path is None:
        raise ValueError(f"Step '{step.name}': missing input {key!r}")
    return path


def execute_csv_figure(
    ctx: Any,
    step: Any,
    *,
    input_key: str,
    input_default: str,
    output_name: str,
    render: Callable[[Path, Path], None],
) -> StepOutput:
    """Resolve a CSV input + PNG output, render, and return the written file."""
    import matplotlib

    matplotlib.use("Agg")
    src = _required_input(ctx, step, input_key, input_default)
    out = _figure_output(ctx, step, output_name)
    render(out, src)
    logger.info("Wrote %s", out)
    return StepOutput(files=[out], metadata={"output": str(out)})


def execute_trace_figure(
    ctx: Any,
    step: Any,
    render: Callable[[Path, Path, Path], None],
    *,
    output_name: str = "fig_trajectory.svg",
) -> StepOutput:
    """Resolve the baseline/guardrail traces + SVG, render, and collect a PDF sidecar."""
    baseline = resolve_trace_path(
        _required_input(ctx, step, "trace_baseline", _DEFAULT_BASELINE)
    )
    guardrail = resolve_trace_path(
        _required_input(ctx, step, "trace_guardrail", _DEFAULT_GUARDRAIL)
    )
    out = _figure_output(ctx, step, output_name)
    render(out, baseline, guardrail)
    logger.info("Wrote %s", out)
    files = [out]
    pdf = out.with_suffix(".pdf")
    if pdf.exists():
        files.append(pdf)
    return StepOutput(files=files, metadata={"output": str(out)})
