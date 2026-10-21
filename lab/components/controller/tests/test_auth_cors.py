#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bearer token and CORS defaults for loopback vs non-loopback binds."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mas.lab.controller.fastapi_app import create_app
from mas.lab.controller.settings import ControllerSettings


def test_token_file_401_then_200(tmp_path: Path):
    token = "s3cret-token"
    app = create_app(ControllerSettings(token=token, host="127.0.0.1"))
    client = TestClient(app)
    assert client.get("/api/info").status_code == 200
    assert client.get("/api/scopes").status_code == 401
    ok = client.get("/api/scopes", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_loopback_cors_permissive():
    app = create_app(ControllerSettings(host="127.0.0.1"))
    client = TestClient(app)
    resp = client.options(
        "/api/info",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code in {200, 204}
    assert resp.headers.get("access-control-allow-origin") in {"*", "http://localhost:5173"}


def test_loopback_cors_allows_docker_ui():
    app = create_app(ControllerSettings(host="127.0.0.1"))
    client = TestClient(app)
    resp = client.options(
        "/api/info",
        headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code in {200, 204}
    assert resp.headers.get("access-control-allow-origin") in {"*", "http://localhost:8080"}


def test_non_loopback_cors_requires_explicit_origin():
    app = create_app(
        ControllerSettings(host="0.0.0.0", token="s3cret-token", cors_origins=["http://ui.test"])
    )
    client = TestClient(app)
    resp = client.options(
        "/api/info",
        headers={"Origin": "http://ui.test", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") == "http://ui.test"


def test_non_loopback_without_token_is_refused():
    with pytest.raises(RuntimeError, match="token-file"):
        create_app(ControllerSettings(host="0.0.0.0"))


def test_loopback_cors_rejects_foreign_origin():
    app = create_app(ControllerSettings(host="127.0.0.1"))
    client = TestClient(app)
    resp = client.options(
        "/api/runs",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert resp.headers.get("access-control-allow-origin") not in {"*", "http://evil.test"}


def test_bearer_rejects_wrong_scheme():
    app = create_app(ControllerSettings(token="s3cret-token", host="127.0.0.1"))
    client = TestClient(app)
    resp = client.get("/api/scopes", headers={"Authorization": "Basic s3cret-token"})
    assert resp.status_code == 401


def test_docs_require_bearer_when_token_set():
    app = create_app(ControllerSettings(token="s3cret-token", host="127.0.0.1"))
    client = TestClient(app)
    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/redoc").status_code == 401
    ok = client.get("/openapi.json", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200


def test_bearer_401_still_sends_cors_allow_origin():
    app = create_app(
        ControllerSettings(token="s3cret-token", host="0.0.0.0", cors_origins=["http://ui.test"])
    )
    client = TestClient(app)
    resp = client.get("/api/scopes", headers={"Origin": "http://ui.test"})
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == "http://ui.test"
