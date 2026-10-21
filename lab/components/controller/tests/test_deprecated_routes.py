#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Old /api/libraries adapters still work and are marked deprecated."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mas.lab.controller.fastapi_app import create_app
from mas.lab.controller.lab_registry import LabRegistry
from mas.lab.controller.manifest_store import ManifestStore

_DEMO = Path(__file__).resolve().parent / "fixtures" / "demo_lab"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    import shutil

    from mas.lab.controller import deps

    dest = tmp_path / "demo"
    shutil.copytree(_DEMO, dest)
    registry = LabRegistry(workspace=None)
    registry._libraries = {"demo": dest}
    registry._library_sources = {"demo": "test-fixture"}
    store = ManifestStore(registry=registry)
    store._libraries = {"demo": dest}
    monkeypatch.setattr(deps, "_manifest_store", store)
    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    return TestClient(create_app())


def test_old_library_list_still_works(client):
    resp = client.get("/api/libraries")
    assert resp.status_code == 200
    assert resp.json()["libraries"][0]["dir"] == "demo"


def test_old_apps_list_still_works(client):
    resp = client.get("/api/libraries/demo/apps")
    assert resp.status_code == 200
    assert "mas_resources" in resp.json()


def test_deprecated_flag_on_old_routes():
    app = create_app()
    spec = app.openapi()
    libraries = spec["paths"]["/api/libraries"]["get"]
    assert libraries.get("deprecated") is True
    jobs = spec["paths"]["/api/jobs"]["get"]
    assert jobs.get("deprecated") is True
