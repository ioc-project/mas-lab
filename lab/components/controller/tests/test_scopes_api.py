#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""GET /api/scopes shape for the five catalog fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    return make_client(store, monkeypatch)


def test_scopes_lists_labs_and_libraries(client):
    data = client.get("/api/scopes").json()
    by_id = {s["id"]: s for s in data["scopes"]}
    assert "lab:x" in by_id
    assert "lab:lonely" in by_id
    assert "library:versioned_lib" in by_id
    assert "library:x_lib" in by_id
    assert "library:readonly" in by_id
    assert "library:root_app" in by_id
    # A lab is never reported as a library.
    assert "library:x" not in by_id
    assert "library:lonely" not in by_id
    for scope_id, scope in by_id.items():
        if scope["kind"] == "lab":
            assert scope_id.startswith("lab:")
        else:
            assert scope_id.startswith("library:")


def test_lab_nests_local_library(client):
    data = client.get("/api/scopes/lab:x").json()
    assert data["kind"] == "lab"
    assert "library:x_lib" in data["libraries"]
    nested = client.get("/api/scopes/library:x_lib").json()
    assert nested["origin"] == "lab"
    assert nested["owner"] == "lab:x"
    assert nested["writable"] is True


def test_origin_writable_kinds(client):
    data = {s["id"]: s for s in client.get("/api/scopes").json()["scopes"]}
    assert data["library:readonly"]["origin"] == "package"
    assert data["library:readonly"]["writable"] is False
    assert data["library:versioned_lib"]["writable"] is True
    assert data["lab:lonely"]["kind"] == "lab"
    assert "experiment" in data["lab:lonely"]["kinds"]
    assert "application" not in data["lab:lonely"]["kinds"]
    assert "application" in data["library:versioned_lib"]["kinds"]
    assert "experiment" not in data["library:versioned_lib"]["kinds"]


def test_visible_libraries_order(client):
    libs = client.get("/api/scopes/lab:x/visible-libraries").json()["libraries"]
    ids = [item["id"] for item in libs]
    assert ids[0] == "library:x_lib"
    assert "library:versioned_lib" in ids
    assert "library:readonly" in ids


def test_list_scopes_includes_counts(client):
    data = {s["id"]: s for s in client.get("/api/scopes").json()["scopes"]}
    assert data["lab:x"]["counts"]["experiment"] >= 1
    assert data["library:versioned_lib"]["counts"]["application"] >= 1


def test_create_lab_with_local_library_and_experiment(tmp_path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    http = make_client(store, monkeypatch)
    resp = http.post(
        "/api/scopes",
        json={"kind": "lab", "name": "fresh", "description": "A new lab"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "lab:fresh"
    assert "library:fresh_lab" in body["libraries"]
    listed = {s["id"]: s for s in http.get("/api/scopes").json()["scopes"]}
    assert "lab:fresh" in listed
    assert "library:fresh_lab" in listed
    created = http.post(
        "/api/scopes/lab:fresh/items",
        json={"kind": "experiment", "name": "trial"},
    )
    assert created.status_code == 201, created.text
    fetched = http.get("/api/scopes/lab:fresh/items/experiment/trial")
    assert fetched.status_code == 200, fetched.text
    conflict = http.post("/api/scopes", json={"kind": "lab", "name": "fresh"})
    assert conflict.status_code == 409
    bad = http.post("/api/scopes", json={"kind": "library", "name": "nope"})
    assert bad.status_code == 400
