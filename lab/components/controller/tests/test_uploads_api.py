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
