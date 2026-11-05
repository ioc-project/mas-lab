#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra-manifest (OtelCollector target) tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.library.telemetry.infra import OtelCollectorTarget, otel_collector_target

_MANIFEST = {
    "apiVersion": "mas/v1",
    "kind": "Infra",
    "metadata": {"name": "local-otel"},
    "spec": {
        "targets": [
            {
                "kind": "OtelCollector",
                "endpoint": "http://localhost:4318",
                "protocol": "otlp-http",
                "app_name": "demo",
            },
            {"kind": "Neo4j", "uri": "bolt://localhost:7687"},
        ]
    },
}


def test_resolve_from_dict():
    t = otel_collector_target(_MANIFEST)
    assert isinstance(t, OtelCollectorTarget)
    assert t.endpoint == "http://localhost:4318"
    assert t.app_name == "demo"


def test_resolve_from_json_file(tmp_path):
    p = tmp_path / "infra.json"
    p.write_text(json.dumps(_MANIFEST))
    assert otel_collector_target(p).endpoint == "http://localhost:4318"


def test_shipped_yaml_manifest():
    # The manifest bundled with the library must parse and expose the endpoint.
    manifest = Path(__file__).resolve().parents[1] / "infra" / "local-otel.yaml"
    t = otel_collector_target(manifest)  # needs pyyaml (verify extra)
    assert t.endpoint == "http://localhost:4318"


def test_endpoint_precedence(monkeypatch):
    t = OtelCollectorTarget(endpoint="http://from-manifest:4318")
    assert t.resolved_endpoint("http://override:4318") == "http://override:4318"  # arg wins
    assert t.resolved_endpoint() == "http://from-manifest:4318"  # manifest
    empty = OtelCollectorTarget(endpoint="")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://from-env:4318")
    assert empty.resolved_endpoint() == "http://from-env:4318"  # env fallback


def test_env_is_shortcut_for_otel_manifest(monkeypatch):
    from mas.library.telemetry.infra import resolve_otel_collector

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://from-env:4318")
    t = resolve_otel_collector()
    assert t.endpoint == "http://from-env:4318"


def test_env_is_shortcut_for_clickhouse_manifest(monkeypatch):
    from mas.library.telemetry.infra import resolve_clickhouse

    monkeypatch.setenv("CLICKHOUSE_HOST", "ch.example")
    monkeypatch.setenv("CLICKHOUSE_PORT", "9000")
    t = resolve_clickhouse()
    assert t.resolved_host() == "ch.example"
    assert t.resolved_port() == 9000


def test_resolve_otel_collector_requires_env_or_infra(monkeypatch):
    from mas.library.telemetry.infra import resolve_otel_collector

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="shortcut"):
        resolve_otel_collector()


def test_missing_target_raises():
    with pytest.raises(ValueError):
        otel_collector_target({"kind": "Infra", "spec": {"targets": [{"kind": "Neo4j"}]}})


def test_push_step_uses_infra(tmp_path):
    from mas.library.telemetry.steps.push_otlp import run_push_otlp

    span = {
        "name": "AgentCall",
        "context": {"span_id": "0x1", "trace_id": "0x2"},
        "start_time": "2024-01-01T00:00:00.000000Z",
        "end_time": "2024-01-01T00:00:01.000000Z",
        "attributes": {"mas.boundary": "AgentCall", "application_id": "demo"},
    }
    spans = tmp_path / "s.jsonl"
    spans.write_text(json.dumps(span) + "\n")
    infra = tmp_path / "infra.json"
    infra.write_text(json.dumps(_MANIFEST))
    result = run_push_otlp(spans, infra=infra, dry_run=True)
    assert result["status"] == "dry-run"
    assert result["spans"] == 1
