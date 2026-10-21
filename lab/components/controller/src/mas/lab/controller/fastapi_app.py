#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
FastAPI server exposing mas-lab as a scope-based REST API.

Run via controller daemon:
    mas-lab serve --port 8090
    mas-lab serve --host 0.0.0.0 --token-file ./token --cors-origin https://ui.example
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mas.lab.controller.constants import LIBRARIES_DIR, MAS_LAB_ROOT
from mas.lab.controller.deps import _manifest_store, get_manifest_store
from mas.lab.controller.jobs import Job, JobStatus, _jobs
from mas.lab.controller.routes import register_routes
from mas.lab.controller.settings import ControllerSettings, is_loopback_host
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Probes and the connect screen must work before a token is entered.
# Swagger and OpenAPI stay behind bearer when a token is configured.
_OPEN_PATHS = {
    "/health",
    "/api/health",
    "/api/info",
}


def _tokens_match(offered: str, expected: str) -> bool:
    """Compare bearer secrets without leaking length via ``hmac.compare_digest``."""
    left = hashlib.sha256(offered.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer`` when a token was configured."""

    def __init__(self, app: FastAPI, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        header = request.headers.get("authorization") or ""
        scheme, _, offered = header.partition(" ")
        got = offered.strip() if scheme.lower() == "bearer" else ""
        if not _tokens_match(got, self._token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Emit discovery report once the API process starts."""
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


def create_app(settings: ControllerSettings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or ControllerSettings()
    application = FastAPI(
        title="MAS Lab API",
        description="REST interface for mas-lab — scope-based HTTP API with persisted runs",
        version=settings.api_version,
        lifespan=lifespan,
    )
    application.state.settings = settings

    # Last-added middleware is outermost. Bearer must sit inside CORS so a
    # 401 JSONResponse still receives Access-Control-Allow-Origin.
    if settings.token:
        application.add_middleware(BearerTokenMiddleware, token=settings.token)
        if not is_loopback_host(settings.host):
            logger.warning(
                "Bearer auth is enabled; clients must send Authorization: Bearer"
            )
    elif not is_loopback_host(settings.host):
        raise RuntimeError(
            f"refusing to bind {settings.host} without --token-file"
        )

    origins = settings.cors_allow_origins
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from mas.lab.controller.job_store import init_job_store
    from mas.lab.controller.run_store import init_run_store
    from mas.lab.controller.uploads import init_upload_store
    from mas.lab.controller.workspace_library import init_workspace_library, reset_workspace_library

    project_root = None
    try:
        from mas.lab.workspace import project_workspace_root

        project_root = project_workspace_root()
    except Exception:
        project_root = None

    if settings.state_dir is not None:
        state_dir: Path = Path(settings.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        init_job_store(db_path=state_dir / "jobs.sqlite")
        init_run_store(state_dir / "runs.sqlite")
        init_upload_store(state_dir / "uploads")
        init_workspace_library(project_root or state_dir)
    else:
        import tempfile

        init_job_store(db_path=None)
        init_run_store(None)
        init_upload_store(Path(tempfile.mkdtemp(prefix="mas-uploads-")))
        if project_root is not None:
            init_workspace_library(project_root)
        else:
            reset_workspace_library()

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
]
