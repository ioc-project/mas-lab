#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EvalMceStep — score one run's events.jsonl into metrics.json."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from mas.lab.benchmark.pipeline.artifact_paths import check_simple_name
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.models import ConfigParam
from mas.lab.benchmark.pipeline.run_artifacts import missing_run_events_message, resolve_run_events, run_dir_from_ctx

logger = logging.getLogger(__name__)

# Many run-scoped instances can score in parallel; this caps judge HTTP calls.
_JUDGE_SEMAPHORES: Dict[int, asyncio.Semaphore] = {}


def _judge_semaphore(max_workers: int) -> asyncio.Semaphore:
    sem = _JUDGE_SEMAPHORES.get(max_workers)
    if sem is None:
        sem = asyncio.Semaphore(max_workers)
        _JUDGE_SEMAPHORES[max_workers] = sem
    return sem


def _resolve_judge(config: dict[str, Any], ctx: ExecutionContext) -> Any:
    """Resolve the judge model without installing the LLM service (no side effects)."""
    from mas.library.eval.mce.judge_model import resolve_judge_model

    backfilled = config.get("model_source") if config.get("model") else None
    step_model = config.get("model")
    evaluation_model = None
    experiment_model = None
    experiment_judge_model = None
    application_model = None
    application_source = "application.spec.models"
    judge_metadata: Optional[Dict[str, Any]] = None
    if backfilled:
        step_model = None
        source = str(backfilled)
        if source.startswith("experiment.evaluation"):
            evaluation_model = config.get("model")
        elif source == "experiment.models.judge":
            experiment_judge_model = config.get("model")
        elif source.startswith("experiment.model"):
            experiment_model = config.get("model")
        elif source.startswith("application"):
            application_model = config.get("model")
            application_source = source
        elif source.startswith("experiment.metadata"):
            judge_metadata = {"model_name": config.get("model")}
    judged = resolve_judge_model(
        step_model=step_model,
        step_judge_model=config.get("judge_model"),
        evaluation_model=evaluation_model,
        metadata=judge_metadata,
        template_vars=getattr(ctx, "template_vars", None),
        application_model=application_model,
        application_source=application_source,
        experiment_model=experiment_model,
        experiment_judge_model=experiment_judge_model,
    )
    return judged


def _install_judge(config: dict[str, Any], ctx: ExecutionContext) -> tuple[str, str]:
    from mas.library.eval.mce.runner import install_openai_llm_service

    judged = _resolve_judge(config, ctx)
    effective = install_openai_llm_service(model_override=judged.model, model_source=judged.source)
    logger.info(
        "EvalMceStep judge model=%s source=%s",
        effective or judged.model or "(infra default)",
        judged.source,
    )
    return effective or judged.model or "", judged.source


_DIGEST_VERSION = "eval_mce.inputs/1"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _metric_impl_version() -> str:
    try:
        from importlib.metadata import version

        return version("mas-library-eval")
    except Exception:
        return ""


def _inputs_digest(
    *,
    events_path: Path,
    run_folder: Path,
    metric_names: list[str],
    prompt_metrics: Any,
    metric_options: Any,
    response_agent: Any,
    judge_model: str,
) -> str:
    """Digest of everything a ``metrics.json`` score was computed from.

    Covers the trace, the optional side files the metrics read, the metric ids
    and definitions, the judge model, the metric implementation version and
    whether the offline stub was active (stub scores must never be reused as
    real ones). ``run_info.json`` is deliberately excluded: it is rewritten
    with fresh timestamps on cached runs and would invalidate every rerun.
    """
    side: dict[str, str] = {}
    for name in ("otel_sdk_spans.jsonl", "otel_sdk_spans_replay.jsonl", "kg.json"):
        candidate = Path(run_folder) / name
        if candidate.exists():
            side[name] = _file_sha256(candidate)
    payload = {
        "v": _DIGEST_VERSION,
        "impl": _metric_impl_version(),
        "offline": os.environ.get("MAS_MCE_OFFLINE", "").lower() in ("1", "true", "yes"),
        "events": _file_sha256(events_path),
        "side": side,
        "metrics": sorted(str(m) for m in metric_names),
        "prompt_metrics": prompt_metrics or [],
        "metric_options": metric_options or {},
        "response_agent": str(response_agent or ""),
        "judge_model": judge_model,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _reuse_decision(metrics_file: Path, digest: str) -> tuple[bool, str]:
    """Decide whether an existing ``metrics.json`` may be reused (``overwrite: false``).

    Reuse only when the stored inputs digest matches. A file written by an
    older version (no digest) is adopted once: stamped with the current
    digest and reused, so historical results are not re-judged. A failed
    scoring (``run_quality.status == "error"``) is never reused.
    """
    try:
        doc = json.loads(metrics_file.read_text(encoding="utf-8"))
    except Exception:
        return False, "existing metrics file is unreadable"
    if not isinstance(doc, dict):
        return False, "existing metrics file is not an object"
    if (doc.get("run_quality") or {}).get("status") == "error":
        return False, "previous scoring failed"
    stored = doc.get("inputs_digest")
    if stored is None:
        doc["inputs_digest"] = digest
        metrics_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.warning(
            "eval_mce: adopted %s (no inputs_digest, written by an older version); "
            "reusing it and stamping the current inputs digest",
            metrics_file,
        )
        return True, "adopted legacy result"
    if stored == digest:
        return True, "inputs unchanged"
    return False, "inputs changed (events, metrics, judge model or version)"


class EvalMceStep(PipelineStep):
    """Score one run into ``metrics.json``.

    The step lists metric ids and writes scores. Each metric implementation
    knows what input it needs (stock MCE: MAS I/O; plugins and inline
    ``prompt_metrics``: their own ``unit`` / ``evidence``).
    """

    type = "eval_mce"
    persistent = True
    resource = "judge"

    PARAMS = [
        ConfigParam("runs_dir", str, default=None, description="Runs tree containing item*/r*/traces/events.jsonl."),
        ConfigParam(
            "response_agent", str, default=None, description="agent_id whose last execution_end is the session answer."
        ),
        ConfigParam(
            "metrics",
            list,
            default=None,
            description="Metric ids to compute. Default: all stock MCE session ids.",
        ),
        ConfigParam("overwrite", bool, default=False, description="Recompute existing metrics.json."),
        ConfigParam("validate", bool, default=True, description="Validate metrics.json against schema."),
        ConfigParam("max_workers", int, default=2, description="Parallel judge threads."),
        ConfigParam(
            "fail_threshold", float, default=1.0, description="Fraction of items that may fail before the step raises."
        ),
        ConfigParam(
            "model",
            str,
            default=None,
            description="LLM-as-judge model. Default: experiment.evaluation.model, "
            "then experiment.models.judge, then experiment.model / "
            "models.main, then application spec.models[].",
        ),
        ConfigParam(
            "metrics_filename",
            str,
            default="metrics.json",
            description="Artefact filename written next to run_info.json.",
        ),
        ConfigParam(
            "metric_options",
            dict,
            default=None,
            description="Per-metric options keyed by metric id (e.g. judge_model override).",
        ),
        ConfigParam(
            "prompt_metrics",
            list,
            default=None,
            description=(
                "Inline metric definitions: {id, prompt, unit, evidence}. "
                "Optional description, system, agent. Same metrics.json as "
                "stock ids. unit/evidence belong to the metric, not the step."
            ),
        ),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.library.eval.mce.catalog import ALL_SESSION_METRICS
        from mas.library.eval.mce.runner import build_metrics_document

        config = self.config
        run_dir = run_dir_from_ctx(ctx, config)
        events_raw = config.get("events_path") or config.get("trace_path")
        events_path = Path(str(events_raw)).expanduser().resolve() if events_raw else resolve_run_events(ctx, config)
        if events_path is None or not events_path.exists():
            raise RuntimeError(missing_run_events_message(self.name, ctx))

        run_folder = Path(run_dir) if run_dir else events_path.parent.parent
        metrics_file = run_folder / check_simple_name(
            self.name, "metrics_filename", config.get("metrics_filename", "metrics.json")
        )
        overwrite = bool(config.get("overwrite", False))

        test = str(config.get("test") or "")
        item_id = test[4:] if test.startswith("item") else test
        if config.get("metrics") is None:
            metric_names = list(ALL_SESSION_METRICS)
        else:
            metric_names = list(config.get("metrics") or [])
        from mas.library.eval.metrics.prompt import parse_prompt_metrics

        prompt_metrics = parse_prompt_metrics(config.get("prompt_metrics"))
        digest = _inputs_digest(
            events_path=events_path,
            run_folder=run_folder,
            metric_names=metric_names,
            prompt_metrics=config.get("prompt_metrics"),
            metric_options=config.get("metric_options"),
            response_agent=config.get("response_agent"),
            judge_model=str(_resolve_judge(config, ctx).model or ""),
        )
        if metrics_file.exists() and not overwrite:
            reuse, reason = _reuse_decision(metrics_file, digest)
            if reuse:
                try:
                    from mas.library.lab.steps.services.metric_event import (
                        flatten_mce_document,
                        publish_metric_record,
                    )

                    doc = json.loads(metrics_file.read_text(encoding="utf-8"))
                    publish_metric_record(
                        "eval_mce",
                        flatten_mce_document(doc),
                        scenario=config.get("scenario") or (doc.get("scenario") if isinstance(doc, dict) else None),
                        test=config.get("test"),
                        run=config.get("run"),
                    )
                except Exception:
                    logger.debug("eval_mce reuse publish skipped", exc_info=True)
                return StepOutput(
                    data={"total": 1, "computed": 0, "skipped": 1, "errors": 0},
                    files=[metrics_file],
                    metadata={"skipped": True, "reuse": reason},
                )
            logger.info("eval_mce: re-scoring %s: %s", metrics_file, reason)

        judge_model, judge_source = _install_judge(config, ctx)

        max_workers = int(config.get("max_workers", 2))
        try:
            async with _judge_semaphore(max_workers):
                session_scores = await _compute_mixed_metrics(
                    events_path=events_path,
                    run_folder=run_folder,
                    metric_names=metric_names,
                    response_agent_id=config.get("response_agent") or None,
                    judge_model=judge_model,
                    judge_source=judge_source,
                    metric_options=config.get("metric_options") or {},
                    prompt_metrics=prompt_metrics,
                )
            _apply_fail_threshold(session_scores, config.get("fail_threshold", 1.0))
            doc = build_metrics_document(
                item_id=item_id,
                scenario=str(config.get("scenario") or ""),
                session_scores=session_scores,
            )
            doc["inputs_digest"] = digest
            if bool(config.get("validate", True)):
                schema = _load_metrics_schema()
                if schema is not None:
                    _validate_document(doc, schema, events_path)
        except Exception as exc:
            logger.error("EvalMceStep '%s': scoring failed for %s: %s", self.name, events_path, exc)
            doc = build_metrics_document(item_id=item_id, scenario=str(config.get("scenario") or ""), session_scores={})
            doc.setdefault("run_quality", {})["status"] = "error"
            doc["run_quality"].setdefault("errors", []).append(str(exc))
            metrics_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
            return StepOutput(
                data={"total": 1, "computed": 0, "skipped": 0, "errors": 1},
                files=[metrics_file],
                metadata={
                    "output": str(metrics_file),
                    "error": str(exc),
                    "judge_model": judge_model,
                    "judge_model_source": judge_source,
                },
            )
        run_ref = run_folder / ".run_ref"
        if run_ref.exists():
            try:
                run_hash = run_ref.read_text(encoding="utf-8").strip()
                doc["run_hash"] = run_hash
                doc["cache_key"] = run_hash
            except Exception:
                logger.debug("suppressed", exc_info=True)
        metrics_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            from mas.library.lab.steps.services.metric_event import (
                flatten_mce_document,
                publish_metric_record,
            )

            publish_metric_record(
                "eval_mce",
                flatten_mce_document(doc),
                scenario=config.get("scenario") or doc.get("scenario"),
                test=config.get("test"),
                run=config.get("run"),
            )
        except Exception:
            logger.debug("eval_mce publish skipped", exc_info=True)
        return StepOutput(
            data={"total": 1, "computed": 1, "skipped": 0, "errors": 0},
            files=[metrics_file],
            metadata={
                "output": str(metrics_file),
                "judge_model": judge_model,
                "judge_model_source": judge_source,
            },
        )

    def outputs_exist(self, output_dir: Path) -> bool:
        return False


async def _compute_mixed_metrics(
    *,
    events_path: Path,
    run_folder: Path,
    metric_names: list[str],
    response_agent_id: Optional[str],
    judge_model: str,
    judge_source: str,
    metric_options: dict[str, Any],
    prompt_metrics: list[Any] | None = None,
) -> dict[str, Any]:
    """Score every requested id through the MCE provider."""
    from mas.library.eval.evaluator import DEFAULT_METRIC_PROVIDER, get_provider
    from mas.library.eval.metrics import MetricContext

    local = list(prompt_metrics or [])
    provider = get_provider(DEFAULT_METRIC_PROVIDER)
    inputs = _run_inputs(run_folder, events_path)
    ctx = MetricContext(
        judge_model=judge_model,
        judge_source=judge_source,
        metric_options=dict(metric_options or {}),
        run_dir=run_folder,
        response_agent_id=response_agent_id,
    )
    session = await provider.compute_metrics(metric_names, inputs, ctx, extra=local)
    qualities = list(ctx.quality_parts)
    qualities.append(_quality_from_scores(session))
    session["__run_quality__"] = _merge_run_quality(*qualities)
    return session


def _run_inputs(run_folder: Path, events_path: Path):
    from mas.library.eval.metrics import RunInputs

    otel = None
    kg = None
    run_info = None
    if run_folder:
        for name in ("otel_sdk_spans.jsonl", "otel_sdk_spans_replay.jsonl"):
            candidate = Path(run_folder) / name
            if candidate.exists():
                otel = candidate
                break
        kg_path = Path(run_folder) / "kg.json"
        if kg_path.exists():
            kg = kg_path
        info_path = Path(run_folder) / "run_info.json"
        if info_path.exists():
            try:
                run_info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                logger.debug("suppressed", exc_info=True)
    return RunInputs(
        run_dir=Path(run_folder) if run_folder else None,
        native_trace=events_path,
        otel_spans=otel,
        kg=kg,
        run_info=run_info,
    )


def _quality_from_scores(session: dict[str, Any]) -> dict[str, Any]:
    errors = [
        f"{mid}: {score.get('error')}"
        for mid, score in session.items()
        if isinstance(score, dict) and score.get("error")
    ]
    return {
        "warnings": [],
        "errors": errors,
        "status": "error" if errors else "ok",
    }


def _merge_run_quality(*parts: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    for part in parts:
        warnings.extend(part.get("warnings") or [])
        errors.extend(part.get("errors") or [])
    status = "error" if errors else ("warn" if warnings else "ok")
    return {"warnings": warnings, "errors": errors, "status": status}


def _apply_fail_threshold(session_scores: dict[str, Any], threshold: Any) -> None:
    try:
        limit = float(threshold)
    except (TypeError, ValueError):
        return
    scored = {k: v for k, v in session_scores.items() if not str(k).startswith("__")}
    if not scored:
        return
    n_err = sum(1 for v in scored.values() if isinstance(v, dict) and v.get("error"))
    if (n_err / len(scored)) > limit:
        raise RuntimeError(f"eval_mce fail_threshold={limit} exceeded: {n_err}/{len(scored)} metrics returned an error")


def _load_metrics_schema() -> Optional[Dict[str, Any]]:
    from mas.lab.schemas.paths import lab_artefact_schema_dir

    schema_path = lab_artefact_schema_dir() / "metrics.schema.json"
    if not schema_path.exists():
        return None
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load metrics schema: %s", exc)
        return None


def _validate_document(doc: Dict[str, Any], schema: Dict[str, Any], trace_path: Path) -> None:
    try:
        import jsonschema

        jsonschema.validate(doc, schema)
    except ImportError:
        logger.debug("jsonschema not installed — skipping metrics validation")
    except Exception as exc:
        logger.warning("metrics.json validation failed for %s: %s", trace_path, exc)
