#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
FastAPI server exposing mas-lab CLI commands as library-scoped REST endpoints.

Run via controller daemon:
    mas-lab control start --port 9000
    mas-lab serve -p 9000

Endpoints are scoped under /api/libraries/{library_name}/:
    /api/libraries/{name}/validate     — validate an agent or MAS manifest
    /api/libraries/{name}/run          — run an agent (single query)
    /api/libraries/{name}/run-mas      — run a MAS (single query)
    /api/libraries/{name}/tools        — list available tools
    /api/libraries/{name}/skills       — list available skills
    /api/libraries/{name}/overlays     — list overlay files
    /api/libraries/{name}/topologies   — list topology files
    /api/libraries/{name}/pipelines    — list pipeline files
    /api/libraries/{name}/experiment/run — in-process experiment worker (live streams)
    /api/libraries/{name}/benchmark/run — run a benchmark experiment
    /api/libraries/{name}/pipeline/run  — run an analysis pipeline
    /api/libraries/{name}/eval-output   — run LLM-as-judge evaluation

Job tracking:
    POST endpoints return a job_id. Use GET /api/jobs to list all jobs,
    GET /api/jobs/{job_id} to poll status, or DELETE /api/jobs/{job_id} to cancel.
    This allows a React client to recover state after a page refresh.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mas.lab.controller.constants import LIBRARIES_DIR, MAS_LAB_ROOT
from mas.lab.controller.deps import (
    _manifest_store,
    discover_skills,
    discover_tools,
    get_manifest_store,
    run_cli,
    validate_overlay_content,
)
from mas.lab.controller.jobs import (
    Job,
    JobStatus,
    _jobs,
    now_iso,
    run_agent_chat_job,
    run_job,
    submit_agent_chat_job,
    submit_job,
)
from mas.lab.controller.pipeline_validation import validate_pipeline_yaml
from mas.lab.controller.routes import register_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_library_path(library_name: str):
    """Resolve a library name to its directory path."""
    from fastapi import HTTPException

    store = get_manifest_store()
    try:
        return store.library_root(library_name)
    except KeyError:
        pass
    lib_dir = LIBRARIES_DIR / library_name
    if not lib_dir.exists() or not lib_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Library '{library_name}' not found")
    return lib_dir


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Emit discovery report once the API process starts."""
    from mas.lab.controller.ui_stream.app import ui_stream_lifespan

    async with ui_stream_lifespan(app):
        if os.environ.get("MAS_CONTROLLER_DISABLE_STARTUP_DISCOVERY", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            yield
            return
        try:
            store = get_manifest_store()
            report = store._registry.discovery_report()
            logger.info("MAS Lab API startup discovery: %s", report)
        except Exception as exc:
            logger.warning("Startup discovery report failed: %s", exc)
        yield


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="MAS Lab API",
        description="REST interface for mas-lab — library-scoped HTTP API with in-process validation and background jobs",
        version="0.3.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_routes(application)
    return application


app = create_app()

__all__ = [
    "app",
    "create_app",
    "Job",
    "JobStatus",
    "LIBRARIES_DIR",
    "MAS_LAB_ROOT",
    "_jobs",
    "_manifest_store",
    "_get_library_path",
]
