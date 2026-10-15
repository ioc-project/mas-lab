#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""EventsToOtelStep — offline replay of events.jsonl to OTel SDK spans JSONL."""

import logging
import shutil
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import ConfigParam, PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class EventsToOtelStep(PipelineStep):
    """Replay native ``events.jsonl`` through library-telemetry's ``replay_events_file``."""

    type = "events_to_otel"

    PARAMS = [
        ConfigParam("events_jsonl", str, description="Path to events.jsonl."),
        ConfigParam("scenario_dir", str, description="Scenario dir for trace discovery."),
        ConfigParam("output_filename", str, default="otel_sdk_spans.jsonl"),
        ConfigParam("service_name", str, default="mas-runtime"),
        ConfigParam("app_name", str, default=None),
        ConfigParam("in_place", bool, default=False),
        ConfigParam("overwrite", bool, default=False),
        ConfigParam(
            "copy_from_replay",
            bool,
            default=False,
            description="Copy otel_path from a dependency instead of replaying events.",
        ),
        ConfigParam(
            "export_layers",
            dict,
            default=None,
            description="Layer toggles: structure, execution, semantic (default on); provenance, governance (default off).",
        ),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.library.telemetry.conversion.exporter import OTEL_AVAILABLE
        from mas.library.telemetry.conversion.replay import replay_events_file

        if not OTEL_AVAILABLE:
            raise RuntimeError(
                "events_to_otel requires mas-library-telemetry[convert] "
                "(opentelemetry-sdk)."
            )

        config = self.config
        output_name = str(config.get("output_filename", "otel_sdk_spans.jsonl"))
        service_name = str(config.get("service_name", "mas-runtime"))
        app_name = str(config.get("app_name") or service_name)
        in_place = bool(config.get("in_place", False))
        overwrite = bool(config.get("overwrite", False))
        copy_from_replay = bool(config.get("copy_from_replay", False))
        export_layers_cfg = config.get("export_layers")

        from mas.library.telemetry.conversion.layers import parse_export_layers

        export_layers = parse_export_layers(
            export_layers_cfg if isinstance(export_layers_cfg, dict) else config
        )

        from mas.lab.benchmark.pipeline.run_artifacts import (
            resolve_run_artifact,
            run_dir_from_ctx,
        )

        replay_src: Path | None = None
        if copy_from_replay:
            replay_src = _resolve_replay_otel_path(ctx, self.depends_on, config)
            if replay_src is None:
                raise FileNotFoundError(
                    f"Step '{self.name}': copy_from_replay set but no replay "
                    "otel_path found on dependencies."
                )

        run_scoped = bool(run_dir_from_ctx(ctx, config))
        trace_paths = [] if copy_from_replay else _resolve_trace_paths(config, ctx, self.depends_on)
        if not copy_from_replay and not trace_paths:
            raise FileNotFoundError(
                f"Step '{self.name}': no events.jsonl found."
            )

        output_paths: list[Path] = []
        total_events = 0

        if copy_from_replay and replay_src is not None:
            run_dir = run_dir_from_ctx(ctx, config)
            if in_place and run_dir:
                out_path = run_dir / "traces" / output_name
            elif run_dir:
                out_path = run_dir / output_name
            else:
                step_dir = ctx.get_step_output_dir(self.name)
                step_dir.mkdir(parents=True, exist_ok=True)
                out_path = step_dir / output_name
            if out_path.exists() and not overwrite:
                output_paths.append(out_path)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(replay_src, out_path)
                logger.info("events_to_otel: copied replay %s → %s", replay_src, out_path)
                output_paths.append(out_path)
        else:
            for events_path in trace_paths:
                if run_scoped and not in_place:
                    out_path = resolve_run_artifact(ctx, "otel_replay_spans")
                elif in_place:
                    out_path = events_path.parent / output_name
                else:
                    step_dir = ctx.get_step_output_dir(self.name)
                    step_dir.mkdir(parents=True, exist_ok=True)
                    if len(trace_paths) == 1:
                        out_path = step_dir / output_name
                    else:
                        out_path = step_dir / f"{events_path.parent.parent.name}_{output_name}"

                if out_path.exists() and not overwrite:
                    output_paths.append(out_path)
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)
                n_events = replay_events_file(
                    events_path,
                    out_path,
                    service_name=service_name,
                    app_name=app_name,
                    export_layers=export_layers,
                )
                total_events += n_events
                output_paths.append(out_path)
                logger.info("events_to_otel: %d events → %s", n_events, out_path)

        if not output_paths:
            raise FileNotFoundError(f"Step '{self.name}': no output path produced.")
        primary = output_paths[0]
        events_ref = str(trace_paths[0]) if trace_paths else ""
        return StepOutput(
            data={
                "otel_path": str(primary),
                "otel_spans_path": str(primary),
                "events_path": events_ref,
                "trace_path": events_ref,
            },
            files=output_paths,
            metadata={
                "events_processed": total_events,
                "trace_count": len(trace_paths),
                "output": str(primary),
            },
        )


def _resolve_trace_paths(
    config: dict[str, Any],
    ctx: ExecutionContext,
    depends_on: list[str],
) -> list[Path]:
    raw = config.get("events_jsonl") or config.get("log_path")
    if raw and str(raw).strip() and str(raw) != "{events_jsonl}":
        path = Path(str(raw))
        if not path.is_absolute():
            path = ctx.output_dir / path
        if path.exists():
            return [path.resolve()]

    templated = _events_from_template(ctx, config)
    if templated is not None:
        return [templated]

    from mas.lab.benchmark.pipeline.run_artifacts import resolve_run_events

    resolved = resolve_run_events(ctx, config)
    if resolved is not None:
        return [resolved]

    scenario_dir = config.get("scenario_dir")
    if scenario_dir:
        return _discover_traces(Path(str(scenario_dir)))

    for dep_name in depends_on:
        dep_out = ctx.step_outputs.get(dep_name)
        if not dep_out:
            continue
        for key in ("trace_path", "events_path", "events_jsonl", "log_path"):
            val = dep_out.data.get(key)
            if val and Path(val).exists():
                return [Path(val).resolve()]

    return _discover_traces(ctx.output_dir)


def _resolve_replay_otel_path(
    ctx: ExecutionContext,
    depends_on: list[str],
    config: dict[str, Any],
) -> Path | None:
    """Resolve replay otel_sdk_spans JSONL from an upstream step or run dir."""
    from mas.lab.benchmark.pipeline.run_artifacts import run_dir_from_ctx

    for dep_name in depends_on:
        dep_out = ctx.step_outputs.get(dep_name)
        if not dep_out:
            continue
        for key in ("otel_path", "otel_spans_path"):
            val = dep_out.data.get(key)
            if val and Path(val).exists():
                return Path(val).resolve()

    run_dir = run_dir_from_ctx(ctx, config)
    if run_dir:
        for candidate in (
            run_dir / "otel_sdk_spans_replay.jsonl",
            run_dir / "traces" / "otel_sdk_spans_replay.jsonl",
        ):
            if candidate.exists():
                return candidate.resolve()
    return None


def _events_from_template(ctx: ExecutionContext, config: dict[str, Any]) -> Path | None:
    run_dir = config.get("run_dir") or ctx.template_vars.get("run_dir", "")
    if run_dir:
        from mas.lab.benchmark.cache.trace_store import resolve_run_events_path

        resolved = resolve_run_events_path(Path(str(run_dir)))
        if resolved is not None:
            return resolved.resolve()
    return None


def _discover_traces(root: Path) -> list[Path]:
    from mas.library.kg.observability.helpers import TRACE_CACHE_ROOT

    traces: list[Path] = []
    if not root.is_dir():
        return traces
    for item_dir in sorted(root.iterdir()):
        if not item_dir.is_dir() or not item_dir.name.startswith("item"):
            continue
        for run_dir in sorted(item_dir.iterdir()):
            if not run_dir.is_dir() or not run_dir.name.startswith("r"):
                continue
            direct = run_dir / "traces" / "events.jsonl"
            if direct.exists():
                traces.append(direct.resolve())
                continue
            ref_file = run_dir / ".run_ref"
            if ref_file.exists():
                run_ref = ref_file.read_text().strip()
                cached = TRACE_CACHE_ROOT / run_ref / "traces" / "events.jsonl"
                if cached.exists():
                    traces.append(cached.resolve())
    return traces
