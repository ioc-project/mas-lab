#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical RUN-scoped artifact registry for benchmark pipelines.

Steps resolve output paths via :func:`resolve_run_artifact` — no hardcoded
base directories.  Per-run materialization injects ``run_dir`` into step config;
:meth:`~mas.lab.benchmark.pipeline.executor.ExecutionContext.scope_context`
must be aligned before calling these helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mas.lab.benchmark.cache.trace_store import resolve_run_events_path
from mas.lab.benchmark.pipeline.resources import Artifact, Scope


@dataclass(frozen=True)
class RunArtifactSpec:
    """Logical run-folder artifact."""

    key: str
    artifact: Artifact
    produced_by: tuple[str, ...] = ()


RUN_ARTIFACTS: Dict[str, RunArtifactSpec] = {
    "events": RunArtifactSpec(
        key="events",
        artifact=Artifact(name="events", format="jsonl", scope=Scope.RUN),
        produced_by=("mas_runtime",),
    ),
    "metrics": RunArtifactSpec(
        key="metrics",
        artifact=Artifact(name="metrics", format="json", scope=Scope.RUN),
        produced_by=("eval_mce",),
    ),
    "dataframe": RunArtifactSpec(
        key="dataframe",
        artifact=Artifact(name="data", format="csv", scope=Scope.RUN),
        produced_by=("metrics_to_dataframe",),
    ),
    "kg": RunArtifactSpec(
        key="kg",
        artifact=Artifact(name="kg", format="json", scope=Scope.RUN),
        produced_by=("normalize_events",),
    ),
    "kg_otel": RunArtifactSpec(
        key="kg_otel",
        artifact=Artifact(name="kg_otel", format="json", scope=Scope.RUN),
        produced_by=("normalize_events",),
    ),
    "trajectory_native": RunArtifactSpec(
        key="trajectory_native",
        artifact=Artifact(name="trajectory-native", format="html", scope=Scope.RUN),
        produced_by=("plot_multilevel_trajectory", "multilevel_trajectory_plotter"),
    ),
    "trajectory_kg": RunArtifactSpec(
        key="trajectory_kg",
        artifact=Artifact(name="trajectory-kg", format="html", scope=Scope.RUN),
        produced_by=("plot_multilevel_trajectory_kg", "multilevel_trajectory_kg_plotter"),
    ),
    "validation_report": RunArtifactSpec(
        key="validation_report",
        artifact=Artifact(name="validation_report", format="json", scope=Scope.RUN),
        produced_by=("validate_kg",),
    ),
    "parity_report": RunArtifactSpec(
        key="parity_report",
        artifact=Artifact(name="parity_report", format="json", scope=Scope.RUN),
        produced_by=("compare_kg",),
    ),
    "otel_replay_spans": RunArtifactSpec(
        key="otel_replay_spans",
        artifact=Artifact(name="otel_sdk_spans_replay", format="jsonl", scope=Scope.RUN),
        produced_by=("events_to_otel",),
    ),
}


def run_dir_from_ctx(ctx: Any, config: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Return the benchmark run folder when scope or config identifies a run."""
    sc = getattr(ctx, "scope_context", None)
    if sc and sc.scenario and sc.test and sc.run:
        return (
            Path(ctx.output_dir) / sc.scenario / sc.test / sc.run
        ).resolve()
    cfg = config or {}
    run_dir_raw = cfg.get("run_dir")
    if run_dir_raw:
        return Path(str(run_dir_raw)).expanduser().resolve()
    return None


def resolve_run_artifact(
    ctx: Any,
    key: str,
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Resolve the on-disk path for a registered RUN-scoped artifact.

    Optional ``artifact_name`` in *config* overrides the filename stem while
    keeping format and scope from the registry entry.
    """
    cfg = config or {}
    spec = RUN_ARTIFACTS.get(key)
    if spec is None:
        raise KeyError(
            f"Unknown run artifact '{key}'. "
            f"Registered: {sorted(RUN_ARTIFACTS)}"
        )
    artifact = spec.artifact
    stem = cfg.get("artifact_name")
    if stem:
        artifact = Artifact(name=str(stem), format=artifact.format, scope=artifact.scope)
    return artifact.resolve_path(ctx)


def resolve_run_events(ctx: Any, config: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Resolve ``events.jsonl`` for the current run (inline trace or cache ref)."""
    run_dir = run_dir_from_ctx(ctx, config)
    if run_dir is None:
        # Standalone ``benchmark pipeline run -o <run folder>``: the output dir
        # itself is a single run folder (it holds ``traces/events.jsonl``).
        out = getattr(ctx, "output_dir", None)
        if out and not (Path(out) / "results.csv").is_file():
            return resolve_run_events_path(Path(out))
        return None
    return resolve_run_events_path(run_dir)


def missing_run_events_message(step_name: str, ctx: Any) -> str:
    """Actionable error for a run-level step that cannot find ``events.jsonl``."""
    out = getattr(ctx, "output_dir", None)
    return (
        f"Step '{step_name}' requires the run-level events artifact "
        "(traces/events.jsonl), but none was found. "
        f"The pipeline output dir (-o) was {str(out) if out else '<not set>'}. "
        "Run-level steps need either a run folder containing traces/events.jsonl, "
        "or an experiment-level benchmark output folder (with results.csv and "
        "<scenario>/<item>/<run>/traces/events.jsonl) from a completed benchmark. "
        "Run it as `mas-lab benchmark pipeline run <pipeline.yaml> -o <benchmark output dir>`, "
        "or attach it to the benchmark with "
        "`mas-lab benchmark run ... --pipeline run:post:<pipeline.yaml>`; "
        "otherwise set `events_path` in the step config."
    )


def run_input_stream(ctx: Any, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the standard per-run input payload injected into step input streams.

    Only run-scoped steps (config has ``run_dir`` or ``run``) receive identity.
    Leftover ``scope_context`` from a batched sibling must not leak into
    item/scenario/experiment gathers.
    """
    cfg = config or {}
    if not (cfg.get("run_dir") or cfg.get("run")):
        return {}
    run_dir = run_dir_from_ctx(ctx, cfg)
    if run_dir is None:
        return {}
    payload: Dict[str, Any] = {"run_dir": str(run_dir)}
    sc = getattr(ctx, "scope_context", None)
    if sc:
        if sc.scenario:
            payload["scenario"] = sc.scenario
        if sc.test:
            payload["test"] = sc.test
        if sc.run:
            payload["run"] = sc.run
    for key in ("scenario", "test", "run"):
        if cfg.get(key) and key not in payload:
            payload[key] = cfg[key]
    events = resolve_run_events(ctx, cfg)
    if events is not None:
        payload["events_path"] = str(events)
        payload["trace_path"] = str(events)
    return payload
