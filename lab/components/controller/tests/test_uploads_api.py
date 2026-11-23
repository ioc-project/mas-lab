#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Upload then use the opaque id (dataset create / import)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.controller.uploads import get_upload_store
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    return make_client(build_store(copy_scope_fixtures(tmp_path)), monkeypatch)


def test_upload_and_dataset_create(client):
    files = {
        "file": (
            "queries.yaml",
            b"apiVersion: lab/v1\nkind: Dataset\nmetadata:\n  name: up\nspec:\n  items: []\n",
            "text/yaml",
        )
    }
    uploaded = client.post("/api/uploads", files=files)
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert "upload_id" in body
    assert "path" not in body
    stored = get_upload_store().get(body["upload_id"])
    assert stored is not None
    assert stored.size == body["size"]
    created = client.post(
        "/api/scopes/lab:x/items",
        json={"kind": "dataset", "name": "up"},
    )
    assert created.status_code == 201, created.text


@pytest.mark.parametrize("name", ["u.yaml.", "u.yaml ", "U.YAML", "u.Json.."])
def test_a_manifest_name_in_another_spelling_is_checked_at_upload(client, name: str):
    canary = "sk-S3CR3T-upload-canary"
    body = f"apiVersion: mas/v1\nkind: Agent\nmetadata: {{name: t}}\nspec:\n  llm:\n    api_key: {canary}\n"
    response = client.post("/api/uploads", files={"file": (name, body.encode(), "text/plain")})
    assert response.status_code == 422
    assert canary not in response.text
    assert not [p for p in get_upload_store().root.iterdir() if canary.encode() in p.read_bytes()]


def test_an_archive_upload_is_kept_unchecked(client):
    response = client.post("/api/uploads", files={"file": ("run.tar.gz", b"\x1f\x8b binary", "application/gzip")})
    assert response.status_code == 201
