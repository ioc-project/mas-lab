#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Item list/get, versions, range resolve, no paths in JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    return make_client(build_store(roots), monkeypatch)


def _assert_no_paths(payload) -> None:
    text = json.dumps(payload)
    assert "apps/" not in text
    assert "/Users/" not in text
    assert "/home/" not in text
    # Windows drive paths in JSON (not YAML ``key:\\n`` escapes).
    assert "C:\\\\" not in text
    for key in ("path", "mas_file", "experiment_path"):
        assert f'"{key}"' not in text


def test_list_applications_groups_versions(client):
    resp = client.get("/api/scopes/library:versioned_lib/items?kind=application")
    assert resp.status_code == 200
    groups = {g["name"]: g for g in resp.json()["items"]}
    assert "triage" in groups
    assert groups["triage"]["versions"] == ["v1", "v2"]
    assert groups["triage"]["latest"] == "v2"
    ids = {i["id"] for i in groups["triage"]["items"]}
    assert ids == {"triage@v1", "triage@v2"}
    assert "plain" in groups
    assert groups["plain"]["versions"] == []
    assert groups["plain"]["latest"] is None
    assert "renamed-app" in groups
    _assert_no_paths(resp.json())


def test_bare_name_resolves_to_latest(client):
    resp = client.get("/api/scopes/library:versioned_lib/items/application/triage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved_id"] == "triage@v2"
    assert body["id"] in {"triage@v2", "triage"}
    assert "manifest" in body
    _assert_no_paths(body)


def test_get_explicit_version(client):
    v1 = client.get("/api/scopes/library:versioned_lib/items/application/triage@v1").json()
    assert v1["version"] == "v1"
    assert "triage" in v1["ref"]


def test_lab_experiments_and_no_apps_on_lab(client):
    exps = client.get("/api/scopes/lab:x/items?kind=experiment")
    assert exps.status_code == 200
    names = {g["name"] for g in exps.json()["items"]}
    assert "smoke" in names
    apps = client.get("/api/scopes/lab:x/items?kind=application")
    assert apps.json()["items"] == []
    lonely = client.get("/api/scopes/lab:lonely/items?kind=experiment").json()
    assert {g["name"] for g in lonely["items"]} == {"only"}
    _assert_no_paths(exps.json())


def test_lab_local_library_apps(client):
    apps = client.get("/api/scopes/library:x_lib/items?kind=application").json()
    assert {g["name"] for g in apps["items"]} == {"hello"}


def test_root_app_library(client):
    apps = client.get("/api/scopes/library:root_app/items?kind=application").json()
    assert any(g["name"] == "rootapp" for g in apps["items"])


def test_range_resolve(client):
    resp = client.get("/api/resolve", params={"ref": "versioned_lib:triage@^v2"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved_id"] == "triage@v2"
    _assert_no_paths(body)


def test_kinds_endpoint(client):
    kinds = {k["id"] for k in client.get("/api/kinds").json()["kinds"]}
    assert "application" in kinds
    assert "experiment" in kinds
