#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schema registry resolves files from runtime/ and lab/components/bench/."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(sample_lab, monkeypatch):
    from mas.lab.controller import fastapi_app
    from mas.lab.controller.manifest_store import ManifestStore

    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}

    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    return TestClient(fastapi_app.app)


@pytest.mark.parametrize(
    "schema_id",
    [
        "agent",
        "mas",
        "overlay",
        "infra",
        "experiment",
        "pipeline",
    ],
)
def test_schema_registry_files_exist(schema_id: str):
    from mas.lab.controller.schema_registry import read_schema_text

    entry, text = read_schema_text(schema_id)
    assert entry.id == schema_id
    assert len(text) > 10


def test_schema_sources_are_checkout_keys():
    from mas.lab.controller.schema_registry import SCHEMA_REGISTRY

    assert {entry.source for entry in SCHEMA_REGISTRY} <= {
        "runtime",
        "bench",
        "core",
        "controller",
    }


def test_list_schemas_includes_runtime_and_bench():
    from mas.lab.controller.schema_registry import list_schemas

    ids = {s["id"] for s in list_schemas()}
    assert "agent" in ids
    assert "infra" in ids
    assert "pipeline-step-types-post" in ids
    assert "agent-editor" not in ids


def test_api_schemas_match_canonical_files(client):
    """Controller must serve the same YAML as docs/schemas/runtime (no drift)."""
    from mas.lab.controller.schema_registry import read_schema_text
    from mas.lab.schemas.paths import runtime_schema_dir

    for schema_id, filename in (("agent", "agent.schema.yaml"), ("mas", "mas.schema.yaml")):
        _, api_text = read_schema_text(schema_id)
        canonical = (runtime_schema_dir() / filename).read_text(encoding="utf-8")
        assert api_text == canonical, f"{schema_id}: API schema differs from {filename}"


def test_api_schemas_resolved_json(client):
    """UI loads resolved JSON (fragment refs inlined server-side)."""
    for schema_id in ("agent", "mas"):
        resp = client.get(f"/api/schemas/{schema_id}?format=json&resolved=1")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body.get("title") or body.get("type")
        dumped = json.dumps(body)
        assert '"$ref": "./' not in dumped


def test_api_schemas_endpoints(client):
    from mas.lab.controller.pipeline_validation import validate_pipeline_yaml

    listed = client.get("/api/schemas").json()
    assert "schemas" in listed
    assert any(s["id"] == "agent" for s in listed["schemas"])

    agent_yaml = client.get("/api/schemas/agent")
    assert agent_yaml.status_code == 200
    assert "text/yaml" in agent_yaml.headers.get("content-type", "")
    assert "kind" in agent_yaml.text or "Agent" in agent_yaml.text

    agent_json = client.get("/api/schemas/agent?format=json")
    assert agent_json.status_code == 200
    assert "application/json" in agent_json.headers.get("content-type", "")
    body = json.loads(agent_json.text)
    assert body.get("title") == "Agent Manifest"

    missing = client.get("/api/schemas/not-a-schema")
    assert missing.status_code == 404

    # pipeline validate uses bench package schema (not a controller-local copy)
    from mas.lab.controller.schema_registry import read_schema_text

    _, pipeline_schema = read_schema_text("pipeline")
    assert "Pipeline" in pipeline_schema
    assert (
        validate_pipeline_yaml(
            "apiVersion: mas/v1\nkind: Pipeline\nmetadata:\n  name: p\n"
            "spec:\n  steps:\n    - name: s1\n      type: noop\n"
        )["valid"]
        is True
    )
