#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Health, info, schema, and metrics endpoints."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from mas.lab.controller.constants import (
    PIPELINE_STEP_TYPES_POST_PATH,
    PIPELINE_STEP_TYPES_PRE_PATH,
)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/api/health")
async def api_health():
    return {"status": "ok"}


@router.get("/api/info")
async def info(request: Request):
    """Capabilities the UI uses to bootstrap a connection."""
    settings = getattr(request.app.state, "settings", None)
    auth = getattr(settings, "auth_mode", "none") if settings else "none"
    api_version = getattr(settings, "api_version", "0.3.0") if settings else "0.3.0"
    cors_ok = True
    if settings is not None:
        cors_ok = bool(settings.cors_allow_origins)
    from mas.lab.controller.deps import store_from_request
    from mas.lab.controller.routes.scopes import _kinds_for
    from mas.lab.controller.scope_catalog import get_scope_catalog

    catalog = get_scope_catalog(store_from_request(request))
    edit_targets = [
        {
            "id": scope.id,
            "kind": scope.kind,
            "name": scope.name,
            "origin": scope.origin,
            "kinds": _kinds_for(scope),
            "owner": scope.owner,
        }
        for scope in catalog.all()
        if scope.writable
    ]
    return {
        "api_version": api_version,
        "features": [
            "scopes",
            "items",
            "runs",
            "artifacts",
            "uploads",
            "choices",
            "sse",
            "edit_targets",
            "create_scope",
        ],
        "auth": auth,
        "cors_ok": cors_ok,
        "writable_default": any(scope.writable for scope in catalog.all()),
        "edit_targets": edit_targets,
    }


@router.get("/api/schemas", tags=["Schemas"])
async def list_manifest_schemas():
    """List manifest schemas resolved from installed packages (runtime, bench, core).

    mas-lab-ui should fetch schemas here instead of bundling copies.
    """
    from mas.lab.controller.schema_registry import list_schemas

    return {"schemas": list_schemas()}


@router.get("/api/schemas/{schema_id}", tags=["Schemas"])
async def get_manifest_schema(
    schema_id: str,
    format: str | None = None,
    resolved: bool = Query(False),
):
    """Return a manifest schema by registry id.

    Query params:
      - ``format=json`` — JSON body (required for ``resolved=1``).
      - ``resolved=1`` — JSON Schema with local ``./`` refs inlined (for UI Ajv).
        Uses the same loader as ``mas.ctl.validate`` — no filesystem access in the browser.
    """
    import yaml as _yaml
    from mas.ctl.validate.schemas import load_schema, schema_path_for_kind
    from mas.lab.controller.schema_registry import read_schema_text

    want_resolved = resolved
    want_json = (format or "").strip().lower() == "json" or want_resolved

    if want_resolved:
        kind = schema_id.replace("-", "_")
        if schema_path_for_kind(kind) is None:
            raise HTTPException(status_code=404, detail=f"No resolved schema for: {schema_id}")
        try:
            schema_dict = load_schema(kind)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(schema_dict)

    try:
        entry, text = read_schema_text(schema_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown schema: {schema_id}") from None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if want_json and entry.format == "yaml":
        text = _json.dumps(_yaml.safe_load(text), indent=2)
        media = "application/json"
    elif entry.format == "json" or want_json:
        media = "application/json"
    else:
        media = "text/yaml"
    return PlainTextResponse(content=text, media_type=media)


@router.get("/api/pipeline-step-types")
async def get_pipeline_step_types(phase: str | None = None):
    """Return the registry of all pipeline step types with config schemas for the React UI.

    Query params:
        phase: "pre" | "post" — return only pre-phase or post-phase step types.
               If omitted, returns the post-phase step types.
    """
    if phase == "pre":
        path = PIPELINE_STEP_TYPES_PRE_PATH
    else:
        path = PIPELINE_STEP_TYPES_POST_PATH
    return _json.loads(path.read_text(encoding="utf-8"))


@router.get("/api/metrics/eval", tags=["Pipelines"])
async def get_eval_metrics():
    """Return available metrics for the annotate_metrics pipeline step.

    Keys are the metric_class values to use in pipeline YAML config.
    Values are human-readable display names.
    Used by step type: annotate_metrics (config.metric_class).
    """
    return {
        "mas.lab.components.evaluation.deepeval_wrapper.AnswerRelevancyMetric": "Answer Relevancy",
        "mas.lab.dataset.evaluator.BiasMetric": "Bias",
    }


@router.get("/api/metrics/mce", tags=["Pipelines"])
async def get_mce_metrics():
    """Return available MCE metrics for eval_batch steps.

    Keys are the metric name strings to use in pipeline YAML config.metrics array.
    Values are human-readable display names.
    Used by step type: eval_batch (config.metrics).
    """
    return {
        "answer_relevancy": "Answer Relevancy",
        "goal_success_rate": "Goal Success Rate",
        "groundedness": "Groundedness",
        "response_completeness": "Response Completeness",
        "task_delegation": "Task Delegation Accuracy",
        "tool_utilization_accuracy": "Tool Utilization Accuracy",
        "duration": "Duration",
    }
