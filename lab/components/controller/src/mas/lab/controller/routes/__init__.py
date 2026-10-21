#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FastAPI route registration for the MAS Lab controller API."""

from __future__ import annotations

from fastapi import FastAPI
from mas.lab.controller.routes import (
    apps,
    artifacts,
    benchmark,
    choices,
    datasets,
    design_patterns,
    eval_output,
    experiments,
    health,
    items,
    jobs,
    libraries,
    overlays,
    pipelines,
    registry,
    run,
    runs,
    scopes,
    uploads,
    validate,
)

_DEPRECATED_PREFIXES = (
    "/api/libraries",
    "/api/jobs",
    "/api/experiments",
)


def register_routes(app: FastAPI) -> None:
    """Attach all API routers to the application."""
    app.include_router(jobs.router)
    app.include_router(registry.router)
    app.include_router(libraries.router)
    app.include_router(overlays.router)
    app.include_router(validate.router)
    app.include_router(run.router)
    app.include_router(datasets.router)
    app.include_router(benchmark.router)
    app.include_router(experiments.router)
    app.include_router(pipelines.router)
    app.include_router(eval_output.router)
    app.include_router(apps.router)
    app.include_router(health.router)
    app.include_router(scopes.router)
    app.include_router(items.router)
    app.include_router(choices.router)
    app.include_router(design_patterns.router)
    app.include_router(runs.router)
    app.include_router(artifacts.router)
    app.include_router(uploads.router)
    _mark_app_deprecated(app)


def _iter_api_routes(node) -> list:
    routes = []
    for route in getattr(node, "routes", []) or []:
        original = getattr(route, "original_router", None)
        if original is not None:
            routes.extend(_iter_api_routes(original))
            continue
        nested = getattr(route, "app", None)
        if nested is not None and hasattr(nested, "routes") and nested is not node:
            routes.extend(_iter_api_routes(nested))
        routes.append(route)
    return routes


def _mark_app_deprecated(app: FastAPI) -> None:
    for route in _iter_api_routes(app):
        path = getattr(route, "path", "") or ""
        if any(path == prefix or path.startswith(prefix + "/") for prefix in _DEPRECATED_PREFIXES):
            if hasattr(route, "deprecated"):
                route.deprecated = True
