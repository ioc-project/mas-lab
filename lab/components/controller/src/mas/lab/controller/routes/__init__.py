#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FastAPI route registration for the MAS Lab controller API."""

from __future__ import annotations

from fastapi import FastAPI
from mas.lab.controller.routes import (
    apps,
    benchmark,
    datasets,
    eval_output,
    experiments,
    health,
    jobs,
    libraries,
    overlays,
    pipelines,
    registry,
    run,
    validate,
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
    from mas.lab.controller.ui_stream import mount_ui_stream

    mount_ui_stream(app)
