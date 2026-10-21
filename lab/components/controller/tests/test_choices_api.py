#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Choices lists: models, design patterns, tools, skills, overlays."""

from __future__ import annotations

from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    return make_client(build_store(copy_scope_fixtures(tmp_path)), monkeypatch)


def test_models_have_default(client):
    data = client.get("/api/choices/models").json()
    assert "models" in data
    assert "default" in data


def test_design_patterns(client):
    data = client.get("/api/choices/design-patterns").json()
    assert "patterns" in data
    # Also available on the dedicated registry route from the keep-list.
    alt = client.get("/api/design-patterns").json()
    assert "patterns" in alt


def test_tools_and_overlays_have_refs(client):
    tools = client.get("/api/choices/tools", params={"scope": "library:versioned_lib"}).json()
    assert tools["tools"]
    assert all("ref" in t and "namespace" in t for t in tools["tools"])
    overlays = client.get("/api/choices/overlays", params={"scope": "library:versioned_lib"}).json()
    assert overlays["overlays"]
    assert all("ref" in o for o in overlays["overlays"])


def test_skills_use_skill_layout_not_rstrip(client):
    data = client.get("/api/choices/skills", params={"scope": "library:versioned_lib"}).json()
    assert "skills" in data
    ids = {row["id"] for row in data["skills"]}
    assert "note" in ids


def test_unknown_scope_is_404(client):
    resp = client.get("/api/choices/tools", params={"scope": "library:missing"})
    assert resp.status_code == 404


def test_step_types_and_runners(client):
    steps = client.get("/api/choices/step-types").json()
    assert "step_types" in steps or "step_types" in str(steps)
    runners = client.get("/api/choices/runners").json()
    assert "runners" in runners
