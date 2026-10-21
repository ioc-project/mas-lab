#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Writable scopes, workspace storage, uploads, and scope config."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mas.lab.controller.fastapi_app import create_app
from mas.lab.controller.settings import ControllerSettings
from mas.lab.controller.workspace_library import (
    WORKSPACE_SCOPE_ID,
    reset_workspace_library,
)
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    return make_client(build_store(roots), monkeypatch), roots


def test_info_edit_targets_are_writable_scopes(client):
    http, _roots = client
    info = http.get("/api/info").json()
    assert "edit_targets" in info
    ids = {row["id"] for row in info["edit_targets"]}
    assert "lab:x" in ids
    assert "library:versioned_lib" in ids
    assert "library:readonly" not in ids
    assert info["writable_default"] is True
    assert "edit_targets" in info["features"]
    assert "create_scope" in info["features"]


def test_copy_into_filtered_by_kind(client):
    http, _roots = client
    resp = http.post(
        "/api/scopes/library:readonly/items/check",
        json={"kind": "application", "name": "fresh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "read_only"
    assert "library:versioned_lib" in body["copy_into"]
    assert "lab:lonely" not in body["copy_into"]


def test_workspace_scope_when_state_dir_set(tmp_path: Path, monkeypatch):
    reset_workspace_library()
    app = create_app(ControllerSettings(state_dir=tmp_path / "state"))
    http = TestClient(app)
    scopes = http.get("/api/scopes").json()["scopes"]
    ids = {row["id"] for row in scopes}
    assert WORKSPACE_SCOPE_ID in ids
    workspace = next(row for row in scopes if row["id"] == WORKSPACE_SCOPE_ID)
    assert workspace["writable"] is True
    assert workspace["origin"] == "workspace"
    created = http.post(
        f"/api/scopes/{WORKSPACE_SCOPE_ID}/items",
        json={"kind": "overlay", "name": "scratch"},
    )
    assert created.status_code == 201, created.text
    assert (tmp_path / "state" / "library" / "overlays" / "scratch.yaml").is_file()
    reset_workspace_library()


def test_workspace_library_uses_project_root(tmp_path: Path, monkeypatch):
    reset_workspace_library()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(project))
    app = create_app(ControllerSettings(state_dir=tmp_path / "state"))
    http = TestClient(app)
    created = http.post(
        f"/api/scopes/{WORKSPACE_SCOPE_ID}/items",
        json={"kind": "overlay", "name": "scratch"},
    )
    assert created.status_code == 201, created.text
    assert (project / "library" / "overlays" / "scratch.yaml").is_file()
    assert not (tmp_path / "state" / "library" / "overlays" / "scratch.yaml").exists()
    reset_workspace_library()


def test_scope_config_uses_basenames(client):
    http, roots = client
    (roots["versioned_lib"] / "infra").mkdir(exist_ok=True)
    (roots["versioned_lib"] / "infra" / "models.yaml").write_text("models: []\n", encoding="utf-8")
    resp = http.get("/api/scopes/library:versioned_lib/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "models.yaml" in body["infra"]
    assert all("/" not in name for name in body["infra"])
    assert all("/" not in name for name in body["flavours"])


def test_upload_size_cap(client, monkeypatch):
    http, _roots = client
    monkeypatch.setattr("mas.lab.controller.constants.MAX_UPLOAD_BYTES", 8)
    files = {"file": ("big.yaml", b"0123456789", "text/yaml")}
    resp = http.post("/api/uploads", files=files)
    assert resp.status_code == 413


def test_dataset_create_uses_upload_id(client):
    http, _roots = client
    uploaded = http.post(
        "/api/uploads",
        files={
            "file": (
                "queries.yaml",
                (
                    b"apiVersion: lab/v1\nkind: Dataset\nmetadata:\n  name: up\n"
                    b"spec:\n  items:\n    - id: item-1\n      inputs:\n        user: hello\n"
                ),
                "text/yaml",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    upload_id = uploaded.json()["upload_id"]
    created = http.post(
        "/api/scopes/lab:x/items",
        json={"kind": "dataset", "name": "up", "upload_id": upload_id},
    )
    assert created.status_code == 201, created.text
    assert "item-1" in created.json()["manifest"]


def test_rename_is_one_batch(client, monkeypatch):
    http, roots = client
    from mas.lab.controller import manifest_layout

    calls: list[int] = []
    original = manifest_layout.apply_writes

    def counted(scope, writes):
        calls.append(len(writes))
        return original(scope, writes)

    monkeypatch.setattr(manifest_layout, "apply_writes", counted)
    monkeypatch.setattr("mas.lab.controller.item_service.apply_writes", counted)
    resp = http.post(
        "/api/scopes/lab:x/items/experiment/smoke/rename",
        json={"name": "smoke-renamed"},
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert not (roots["x"] / "experiments" / "smoke.yaml").exists()
    assert (roots["x"] / "experiments" / "smoke-renamed.yaml").is_file()
