#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark-pipeline adapters for the KG steps (optional ``[bench]`` extra).

Thin ``PipelineStep`` wrappers that make the library's pure step functions
(:mod:`mas.library.kg.steps`) usable inside a ``mas-lab`` benchmark pipeline.
**All logic lives in the library** — an adapter only maps a pipeline
``ExecutionContext``/config to a ``run_*`` call and back to a ``StepOutput``.

Discovery is via this library's ``library.yaml`` manifest (``plugins:`` entries
of ``type: step``); the runtime plugin registry resolves each step lazily at
startup, and this module — like every step module — does all its imports at top
so any import problem surfaces at startup, never mid-execution.

Requires the bench framework (``lab/components/bench``); optional extra::

    this library's ``bench`` extra
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.run_artifacts import run_dir_from_ctx

from mas.library.kg.infra import resolve_neo4j_conn
from mas.library.kg.steps.compare_kg import run_compare_kg
from mas.library.kg.steps.neo4j_dump import run_neo4j_dump
from mas.library.kg.steps.neo4j_push import run_neo4j_push
from mas.library.kg.steps.normalize import run_normalize
from mas.library.kg.steps.normalize_otel import run_normalize_otel
from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg
from mas.library.kg.steps.verify_events import run_verify_events

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

__all__ = [
    "NormalizeEventsStep",
    "NormalizeOtelStep",
    "ValidateKgStep",
    "VerifyEventsStep",
    "CompareKgStep",
    "Neo4jPushStep",
    "Neo4jDumpStep",
]


# --------------------------------------------------------------------------- #
# Shared ctx helpers
# --------------------------------------------------------------------------- #


def _run_dir(ctx: "ExecutionContext", config: dict) -> Optional[Path]:
    return run_dir_from_ctx(ctx, config)


def _run_file(ctx: "ExecutionContext", config: dict, filename: str) -> Optional[str]:
    rd = _run_dir(ctx, config)
    if not rd:
        return None
    for sub in (rd / "traces" / filename, rd / filename):
        if sub.exists():
            return str(sub)
    return None


def _dep(ctx: "ExecutionContext", depends_on, key: str) -> Optional[str]:
    for dep_name in depends_on:
        out = ctx.step_outputs.get(dep_name)
        if out and key in out.data:
            return out.data[key]
    return None


def _run_id(ctx: "ExecutionContext", config: dict) -> str:
    rid = config.get("run_id")
    if rid:
        return str(rid)
    rd = _run_dir(ctx, config)
    return rd.name if rd else "run"


# --------------------------------------------------------------------------- #
# events.jsonl → kg.jsonld
# --------------------------------------------------------------------------- #


class NormalizeEventsStep(PipelineStep):
    """``events.jsonl`` → KG artifact — wraps ``steps.run_normalize``."""

    type = "normalize_events"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        events_path = (
            config.get("events_path")
            or _run_file(ctx, config, config.get("filename") or "events.jsonl")
            or _dep(ctx, self.depends_on, "events_path")
        )
        if not events_path or not Path(events_path).exists():
            raise FileNotFoundError(f"Step '{self.name}': events.jsonl not found ({events_path})")
        output_dir = ctx.get_step_output_dir(self.name)
        artifact = run_normalize(
            events_path,
            run_id=_run_id(ctx, config),
            output_dir=output_dir,
            ontology_path=config.get("ontology_path"),
            # Observability gaps (unmapped event kinds, missing call_id, …)
            # should never crash a benchmark run — log a warning and skip
            # the offending event by default. Set `strict: true` in the
            # step config to instead fail fast (e.g. CI conformance runs).
            strict=bool(config.get("strict", False)),
            include_infrastructure=bool(config.get("include_infrastructure", False)),
            include_trajectory=bool(config.get("include_trajectory", True)),
            include_provenance=bool(config.get("include_provenance", False)),
            include_governance=bool(config.get("include_governance", False)),
            include_annotation=bool(config.get("include_annotation", False)),
        )
        kg_path = output_dir / "kg.jsonld"
        return StepOutput(
            data={
                "kg_path": str(kg_path),
                "artifact": config.get("artifact", "kg"),
                "node_count": artifact.node_count,
                "edge_count": artifact.edge_count,
            },
            files=[kg_path],
            metadata={"step": self.name, "step_type": self.type},
        )


class NormalizeOtelStep(PipelineStep):
    """OTel spans → events → KG artifact — wraps ``steps.run_normalize_otel``."""

    type = "normalize_otel"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        spans_path = (
            config.get("spans_path")
            or _run_file(ctx, config, config.get("filename") or "otel_sdk_spans.jsonl")
            or _dep(ctx, self.depends_on, "otel_path")
        )
        if not spans_path or not Path(spans_path).exists():
            raise FileNotFoundError(f"Step '{self.name}': spans file not found ({spans_path})")
        output_dir = ctx.get_step_output_dir(self.name)
        artifact = run_normalize_otel(
            spans_path,
            run_id=_run_id(ctx, config),
            output_dir=output_dir,
            ontology_path=config.get("ontology_path"),
            # Same "observability gap should never crash" default as
            # NormalizeEventsStep above; opt into `strict: true` for CI
            # conformance checks of the mapping tables.
            strict=bool(config.get("strict", False)),
        )
        kg_path = output_dir / "kg.jsonld"
        events_path = output_dir / "events.jsonl"
        otel_format = artifact.metadata.get("otel_format")
        event_count = artifact.metadata.get("event_count")
        out = StepOutput(
            data={
                "kg_path": str(kg_path),
                "events_path": str(events_path),
                "artifact": config.get("artifact", "kg_otel"),
                "node_count": artifact.node_count,
                "event_count": event_count,
                "otel_format": otel_format,
            },
            files=[kg_path],
            metadata={"step": self.name, "step_type": self.type, "otel_format": otel_format},
        )
        if otel_format == "unknown":
            # Distinguish "empty because the span format was unrecognized"
            # from "empty because the trace was genuinely empty" — both
            # produce node_count == 0, but only this one is a real gap.
            out.warnings.append(
                f"Step '{self.name}': span format could not be determined "
                f"({spans_path}) — produced an empty KG instead of failing "
                "the run. This is very likely a real gap, not an empty trace."
            )
        return out


# --------------------------------------------------------------------------- #
# validation / verification
# --------------------------------------------------------------------------- #


class ValidateKgStep(PipelineStep):
    """Validate a KG (structural + SHACL) — wraps ``steps.run_validate_kg``."""

    type = "validate_kg"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        kg_path = config.get("kg_path") or _dep(ctx, self.depends_on, "kg_path")
        if not kg_path:
            raise ValueError(f"Step '{self.name}': no KG artifact to validate")
        # run_validate_kg always raises KGValidationError on a nonzero
        # error_count (no fail_on_error escape hatch on the library function
        # itself) -- this step still writes the report to disk either way,
        # and only re-raises (aborting the pipeline) if its own
        # `fail_on_error` config says to.
        step_fail_on_error = bool(config.get("fail_on_error", False))
        try:
            report = run_validate_kg(
                kg_path,
                ontology_path=config.get("ontology_path"),
                strict=bool(config.get("strict", False)),
            )
        except KGValidationError as exc:
            report = exc.report
        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "kg_validation_report.json"
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if step_fail_on_error and report.get("error_count"):
            raise KGValidationError(report)
        return StepOutput(
            data={
                "error_count": report.get("error_count", 0),
                "kg_path": str(kg_path),
                "report_path": str(report_file),
            },
            files=[report_file],
            metadata={"step": self.name, "step_type": self.type},
        )


class VerifyEventsStep(PipelineStep):
    """Validate ``events.jsonl`` — wraps ``steps.run_verify_events``."""

    type = "verify_events"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        events_path = (
            config.get("events_path")
            or _run_file(ctx, config, config.get("filename") or "events.jsonl")
            or _dep(ctx, self.depends_on, "events_path")
        )
        if not events_path:
            raise ValueError(f"Step '{self.name}': no events.jsonl to verify")
        report = run_verify_events(
            events_path,
            strictness=str(config.get("strictness", "required")),  # type: ignore[arg-type]
            fail_on_error=bool(config.get("fail_on_error", False)),
        )
        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "events_verify_report.json"
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return StepOutput(
            data={"error_count": report.get("error_count", 0), "report_path": str(report_file)},
            files=[report_file],
            metadata={"step": self.name, "step_type": self.type},
        )


class CompareKgStep(PipelineStep):
    """Structural comparison of two KGs — wraps ``steps.run_compare_kg``."""

    type = "compare_kg"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        ref_art = config.get("reference_artifact", "kg")
        cand_art = config.get("candidate_artifact", "kg_otel")
        candidate = config.get("candidate_kg_path")
        reference = config.get("reference_kg_path")
        # Resolve from named dependency outputs when configured by artifact name.
        for dep_name in self.depends_on:
            out = ctx.step_outputs.get(dep_name)
            if not out:
                continue
            if out.data.get("artifact") == ref_art and out.data.get("kg_path"):
                reference = reference or out.data["kg_path"]
            if out.data.get("artifact") == cand_art and out.data.get("kg_path"):
                candidate = candidate or out.data["kg_path"]
        if not candidate or not reference:
            raise ValueError(f"Step '{self.name}': need candidate + reference KG artifacts")
        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "kg_compare_report.json"
        raw_types = config.get("node_types")
        node_types = [str(t) for t in raw_types] if isinstance(raw_types, list) else None
        report = run_compare_kg(
            candidate,
            reference,
            strict=bool(config.get("strict", False)),
            fail_on_error=bool(config.get("fail_on_error", False)),
            output_path=report_file,
            node_types=node_types,
        )
        if bool(config.get("fail_on_error", False)):
            ref_n = int((report.get("reference_stats") or {}).get("nodes") or 0)
            cand_n = int((report.get("candidate_stats") or {}).get("nodes") or 0)
            if ref_n == 0 or cand_n == 0:
                raise ValueError(
                    f"Step '{self.name}': compare_kg saw an empty graph "
                    f"(reference={ref_n} nodes, candidate={cand_n} nodes)"
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


# --------------------------------------------------------------------------- #
# Neo4j serialization
# --------------------------------------------------------------------------- #


class Neo4jPushStep(PipelineStep):
    """Push a KG to Neo4j — wraps ``steps.run_neo4j_push``."""

    type = "neo4j_push"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        kg_path = config.get("kg_path") or _dep(ctx, self.depends_on, "kg_path")
        if not kg_path:
            raise ValueError(f"Step '{self.name}': no KG artifact to push")
        conn = _neo4j_conn(config)
        result = run_neo4j_push(
            kg_path,
            app_name=str(config.get("app_name") or ""),
            batch_size=int(config.get("batch_size", 200)),
            clear_session=bool(config.get("clear_session", False)),
            dry_run=bool(config.get("dry_run", False)),
            **conn,
        )
        return StepOutput(data=result, metadata={"step": self.name, "step_type": self.type})


class Neo4jDumpStep(PipelineStep):
    """Fetch a KG from Neo4j — wraps ``steps.run_neo4j_dump``."""

    type = "neo4j_dump"

    async def execute(self, ctx: "ExecutionContext") -> "StepOutput":
        config = self.config
        output_dir = ctx.get_step_output_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "kg.jsonld"
        conn = _neo4j_conn(config)
        artifact = run_neo4j_dump(
            session_id=config.get("session_id"),
            run_id=config.get("run_id"),
            output_path=out_path,
            **conn,
        )
        return StepOutput(
            data={"kg_path": str(out_path), "node_count": artifact.node_count},
            files=[out_path],
            metadata={"step": self.name, "step_type": self.type},
        )


def _neo4j_conn(config: dict) -> dict:
    """Resolve neo4j connection from an --infra manifest or explicit config.

    Thin adapter over ``infra.resolve_neo4j_conn`` (shared with ``lab_cli.py``'s
    ``neo4j-push``/``neo4j-dump`` commands) for this step's dict-shaped config.
    """
    return resolve_neo4j_conn(
        infra=config.get("infra"),
        uri=config.get("uri"),
        username=config.get("username"),
        password_env=config.get("password_env"),
        database=config.get("database"),
    )
