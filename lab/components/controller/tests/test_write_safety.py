#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Names, traversal, and read-only copy_into."""

from __future__ import annotations

from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    return make_client(build_store(copy_scope_fixtures(tmp_path)), monkeypatch)


@pytest.mark.parametrize("name", ["../escape", "/abs", ".hidden", "a/b", "foo\\bar"])
def test_rejects_unsafe_names(client, name):
    resp = client.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "application", "name": name},
    )
    assert resp.status_code in {400, 422}


def test_read_only_returns_copy_into(client):
    resp = client.post(
        "/api/scopes/library:readonly/items",
        json={"kind": "application", "name": "nope"},
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["reason"] == "read_only"
    assert any(item.startswith("library:") for item in detail["copy_into"])
    assert "library:readonly" not in detail["copy_into"]


def test_rejects_path_like_item_id(client):
    resp = client.get("/api/scopes/library:versioned_lib/items/application/foo..bar")
    assert resp.status_code == 400
    assert "/Users/" not in resp.text
    assert "invalid" in resp.text.lower()


def test_dot_dir_yaml_not_listed(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    staging = roots["versioned_lib"] / ".mas-write-xyz"
    staging.mkdir()
    (staging / "ghost.yaml").write_text(
        "apiVersion: mas/v1\nkind: Overlay\nmetadata:\n  name: ghost\n",
        encoding="utf-8",
    )
    http = make_client(build_store(roots), monkeypatch)
    items = http.get(
        "/api/scopes/library:versioned_lib/items", params={"kind": "overlay"}
    ).json()["items"]
    assert "ghost" not in {row["name"] for row in items}


def test_check_rejects_existing(client):
    resp = client.post(
        "/api/scopes/library:versioned_lib/items/check",
        json={"kind": "application", "name": "plain"},
    )
    assert resp.json()["ok"] is False
