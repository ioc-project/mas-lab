#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mas.lab.controller.ui_stream.app import mount_ui_stream
from mas.lab.controller.ui_stream.auth import assert_ui_bind


@pytest.fixture
def authed_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MAS_DEMO_UI_TOKEN", "secret")
    app = FastAPI()
    mount_ui_stream(app)
    return TestClient(app)


def test_loopback_bind_ok_without_tls() -> None:
    assert_ui_bind("127.0.0.1")
    assert_ui_bind("localhost")


def test_non_loopback_bind_requires_tls_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAS_DEMO_UI_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("MAS_DEMO_UI_SSL_KEYFILE", raising=False)
    monkeypatch.delenv("MAS_DEMO_UI_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TLS"):
        assert_ui_bind("0.0.0.0")
    monkeypatch.setenv("MAS_DEMO_UI_SSL_CERTFILE", "/tmp/cert.pem")
    monkeypatch.setenv("MAS_DEMO_UI_SSL_KEYFILE", "/tmp/key.pem")
    with pytest.raises(RuntimeError, match="MAS_DEMO_UI_TOKEN"):
        assert_ui_bind("10.0.0.8")
    monkeypatch.setenv("MAS_DEMO_UI_TOKEN", "secret")
    assert_ui_bind("10.0.0.8") is None


def test_ingest_and_commands_require_bearer(authed_client: TestClient) -> None:
    bare = authed_client.post("/api/ingest", json={"records": []})
    assert bare.status_code == 401
    ok = authed_client.post(
        "/api/ingest",
        json={"records": []},
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200
    cmd = authed_client.post("/api/commands", json={"verb": "reset-view"})
    assert cmd.status_code == 401
    cmd_ok = authed_client.post(
        "/api/commands",
        json={"verb": "reset-view", "ref": {"origin": "demo-ui"}},
        headers={"Authorization": "Bearer secret"},
    )
    assert cmd_ok.status_code == 200


def test_ws_hello_token(authed_client: TestClient) -> None:
    with authed_client.websocket_connect("/ws") as ws:
        ws.send_json({"topics": ["spec.*"], "token": "secret"})
        # catch-up may be empty; connection stays open
        ws.close()
    with authed_client.websocket_connect("/ws") as ws:
        ws.send_json({"topics": ["spec.*"]})
        denied = ws.receive_json()
        assert denied.get("error") == "unauthorized"


def test_security_headers_present(authed_client: TestClient) -> None:
    res = authed_client.get("/api/layouts", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"


def test_ingest_rejects_huge_payload(authed_client: TestClient) -> None:
    res = authed_client.post(
        "/api/ingest",
        json={"records": [{}] * 20_001},
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 413
