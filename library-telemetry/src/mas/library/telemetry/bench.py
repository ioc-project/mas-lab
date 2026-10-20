#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark-pipeline adapters for the telemetry steps (optional ``[bench]`` extra).

These are the thin ``PipelineStep`` wrappers that make the library's pure step
functions (:mod:`mas.library.telemetry.steps`) usable inside a ``mas-lab``
benchmark pipeline.  **All logic lives in the library** — an adapter only maps a
pipeline ``ExecutionContext``/config to a ``run_*`` call and back to a
``StepOutput``.

Discovery is via this library's ``library.yaml`` manifest (``plugins:`` entries
of ``type: step``), so the bench framework finds these steps without importing
the library eagerly — and the library never imports ``mas.runtime``.

Importing this module requires the bench framework (``mas-lab-bench``); it is an
optional extra::

    uv pip install -e "mas-library-telemetry[bench]"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# All imports at module top: this module is imported when the bench factory
# resolves one of its steps at startup, so any import problem surfaces there —
# never mid-execution. (The framework base is import-light and mas.runtime-free.)
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import check_simple_name
from mas.lab.benchmark.pipeline.run_artifacts import run_dir_from_ctx

from mas.library.telemetry.artifact import OtelSpanSet
from mas.library.telemetry.conversion.layers import parse_export_layers
from mas.library.telemetry.steps.compare_spans import run_compare_spans
from mas.library.telemetry.steps.convert import run_convert
from mas.library.telemetry.steps.push_otlp import run_push_otlp
from mas.library.telemetry.steps.verify_spans import run_verify_spans

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

__all__ = [
    "EventsToOtelStep",
    "VerifyOtelStep",
    "CompareOtelSpansStep",
    "ExportOtelStep",
    "ClickhouseDumpStep",
]


def _resolve_run_file(
    ctx: "ExecutionContext", config: dict, filename: str
) -> Optional[str]:
    """Resolve ``<run_dir>/traces/<filename>`` (or ``<run_dir>/<filename>``)."""
    run_dir = run_dir_from_ctx(ctx, config)
    if not run_dir:
        return None
    for sub in (run_dir / "traces" / filename, run_dir / filename):
        if sub.exists():
            return str(sub)
    return None


def _dep_path(ctx: "ExecutionContext", depends_on, key: str) -> Optional[str]:
    for dep_name in depends_on:
        dep_out = ctx.step_outputs.get(dep_name)
        if dep_out and key in dep_out.data:
            return dep_out.data[key]
    return None


class EventsToOtelStep(PipelineStep):
    """``events.jsonl`` → ``otel_sdk_spans.jsonl`` — wraps ``steps.run_convert``.

    Registered as ``events_to_otel``.
    """

    type = "events_to_otel"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        events_path = (
            config.get("events_path")
            or _resolve_run_file(ctx, config, config.get("filename") or "events.jsonl")
            or _dep_path(ctx, self.depends_on, "events_path")
        )
        if not events_path or not Path(events_path).exists():
            raise FileNotFoundError(
                f"Step '{self.name}': events.jsonl not found ({events_path})"
            )

        output_dir = ctx.get_step_output_dir(self.name)
        out_name = check_simple_name(
            self.name, "output_filename", config.get("output_filename") or "otel_sdk_spans.jsonl"
        )
        layers = parse_export_layers(config)
        span_set = run_convert(
            events_path,
            output_dir=output_dir,
            output_filename=out_name,
            service_name=str(config.get("service_name") or ""),
            app_name=str(config.get("app_name") or ""),
            export_layers=layers,
            converter_profile=str(config.get("converter_profile") or "observe_sdk"),
            realtime=bool(config.get("realtime", False)),
            replay_speed=float(config.get("replay_speed") or 0),
            extensions=(
                None
                if config.get("extensions") is None
                else bool(config.get("extensions"))
            ),
            shift_to_now=bool(config.get("shift_to_now", False)),
            new_session_id=bool(config.get("new_session_id", False)),
        )
        out_path = output_dir / out_name
        logger.info("Step '%s': converted → %d spans", self.name, span_set.span_count)
        return StepOutput(
            data={"otel_path": str(out_path), "span_count": span_set.span_count},
            files=[out_path],
            metadata={"step": self.name, "step_type": self.type},
        )


class VerifyOtelStep(PipelineStep):
    """Validate ``otel_sdk_spans.jsonl`` — wraps ``steps.run_verify_spans``."""

    type = "verify_otel"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        fail_on_err = bool(config.get("fail_on_error", False))
        spans_path = (
            config.get("spans_path")
            or (
                _resolve_run_file(ctx, config, config["spans_filename"])
                if config.get("spans_filename") or config.get("filename")
                else None
            )
            or _dep_path(ctx, self.depends_on, "otel_path")
        )
        if not spans_path or not Path(spans_path).exists():
            raise FileNotFoundError(
                f"Step '{self.name}': spans file not found ({spans_path})"
            )

        report = run_verify_spans(
            spans_path,
            spanspec_level=str(config.get("spanspec_level", "L3")),
            spanspec_path=config.get("spanspec_path"),
            spanspec_strictness=str(config.get("spanspec_strictness", "required")),
            fail_on_error=bool(config.get("spanspec_fail_on_error", False)),
        )

        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "otel_verify_report.json"
        report_file.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if fail_on_err and not report["ok"]:
            raise RuntimeError(
                f"Step '{self.name}': OTel verification failed — "
                f"{len(report['errors'])} error(s). See {report_file}"
            )
        out = StepOutput(
            data={
                "ok": report["ok"],
                "span_count": report["stats"].get("total_spans", 0),
                "report_path": str(report_file),
                "otel_path": str(spans_path),
            },
            files=[report_file],
            metadata={"step": self.name, "step_type": self.type, "ok": report["ok"]},
        )
        out.warnings.extend(report.get("warnings", []))
        return out


class CompareOtelSpansStep(PipelineStep):
    """Structural parity of two span files — wraps ``steps.run_compare_spans``."""

    type = "compare_otel_spans"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        reference = config.get("reference") or _dep_path(
            ctx, self.depends_on, "otel_path"
        )
        candidate = config.get("candidate")
        if config.get("candidate_filename"):
            candidate = (
                _resolve_run_file(ctx, config, config["candidate_filename"])
                or candidate
            )
        if not reference or not candidate:
            raise ValueError(
                f"Step '{self.name}': compare needs 'reference' and 'candidate' span files"
            )
        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "otel_parity_report.json"
        report = run_compare_spans(
            reference,
            candidate,
            strict=bool(config.get("strict", True)),
            output_path=report_file,
            fail_on_error=bool(config.get("assert_equivalent", False)),
        )
        if config.get("assert_equivalent") and not report.get("passed"):
            raise RuntimeError(
                f"Step '{self.name}': span parity FAILED. See {report_file}"
            )
        return StepOutput(
            data={
                "passed": report.get("passed"),
                "summary": report.get("summary"),
                "report_path": str(report_file),
            },
            files=[report_file],
            metadata={"step": self.name, "step_type": self.type},
        )


class ExportOtelStep(PipelineStep):
    """Serialize spans to an OTLP collector — wraps ``steps.run_push_otlp``.

    Registered as ``export_otel``.
    """

    type = "export_otel"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        spans_path = (
            config.get("spans_path")
            or (
                _resolve_run_file(ctx, config, config["spans_filename"])
                if config.get("spans_filename")
                else None
            )
            or _dep_path(ctx, self.depends_on, "otel_path")
        )
        if not spans_path:
            raise ValueError(f"Step '{self.name}': no spans file to export")
        result = run_push_otlp(
            spans_path,
            endpoint=config.get("endpoint"),
            infra=config.get("infra"),
            service_name=str(config.get("service_name") or ""),
            app_name=str(config.get("app_name") or ""),
            dry_run=bool(config.get("dry_run", False)),
            shift_to_now=bool(config.get("shift_to_now", False)),
            new_session_id=bool(config.get("new_session_id", False)),
        )
        if result.get("status") not in {"ok", "dry-run"}:
            raise RuntimeError(
                f"Step '{self.name}': OTLP export failed — {result.get('detail')}"
            )
        return StepOutput(
            data=result,
            metadata={"step": self.name, "step_type": self.type},
        )


class ClickhouseDumpStep(PipelineStep):
    """Dump a session's spans from ClickHouse → OtelSpanSet artifact.

    The read-back counterpart of ``export_otel`` (dump == deserialize).  It
    produces an :class:`OtelSpanSet` artifact and serialises it to a file under
    the step output dir; downstream steps consume the artifact (``otel_path``).
    """

    type = "clickhouse_dump"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        session_id = config.get("session_id") or config.get("run_id")
        if not session_id:
            raise ValueError(
                f"Step '{self.name}': 'session_id' (or 'run_id') is required"
            )
        span_set: OtelSpanSet = OtelSpanSet.fetch_from_clickhouse(
            session_id,
            query_by=str(config.get("by", "session")),
            app_name=config.get("app_name"),
            table=str(config.get("table", "otel_traces")),
        )
        output_dir = ctx.get_step_output_dir(self.name)
        out_path = span_set.save(
            output_dir / check_simple_name(
                self.name, "output_filename", config.get("output_filename") or "otel_sdk_spans.jsonl"
            )
        )
        return StepOutput(
            data={"otel_path": str(out_path), "span_count": span_set.span_count},
            files=[out_path],
            metadata={"step": self.name, "step_type": self.type},
        )
