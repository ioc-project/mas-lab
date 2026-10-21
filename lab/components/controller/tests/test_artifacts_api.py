#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Only issued artifact ids are readable; traversal ids are rejected."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mas.lab.controller.artifacts import ArtifactRegistry, set_artifact_registry
from mas.lab.controller.fastapi_app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    registry = ArtifactRegistry()
    set_artifact_registry(registry)
    run_dir = tmp_path / "out"
    run_dir.mkdir()
    (run_dir / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (run_dir / "notes.yaml").write_text("ok: true\n", encoding="utf-8")
    arts = registry.register_tree("run-1", run_dir)
    app = create_app()
    return TestClient(app), arts, registry


def test_issued_ids_readable(client):
    http, arts, _reg = client
    listed_shape = [a.public_dict() for a in arts]
    assert all("artifact_id" in a and "name" in a for a in listed_shape)
    csv = next(a for a in arts if a.name == "results.csv")
    resp = http.get(f"/api/artifacts/{csv.artifact_id}")
    assert resp.status_code == 200
    assert "1,2" in resp.text


def test_unknown_and_traversal_rejected(client):
    http, _arts, _reg = client
    assert http.get("/api/artifacts/not-issued").status_code == 404
    assert http.get("/api/artifacts/../etc/passwd").status_code == 404


def test_media_types(client):
    http, arts, _reg = client
    yaml_art = next(a for a in arts if a.name.endswith(".yaml"))
    assert "yaml" in yaml_art.media_type or yaml_art.kind == "yaml"
