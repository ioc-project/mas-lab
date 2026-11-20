#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""export_otel finds its OTLP endpoint in the host environment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.services import export_otel as eo
from mas.runtime import hostenv


def test_otlp_endpoint_comes_from_the_host_environment() -> None:
    with hostenv.overlay({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}):
        assert eo._resolve_endpoint("otlp", {}) == "http://collector:4318"
    assert eo._resolve_endpoint("otlp", {"endpoint": "http://own:1"}) == "http://own:1"
    with hostenv.overlay({"OTEL_EXPORTER_OTLP_ENDPOINT": ""}), pytest.raises(ValueError, match="requires 'endpoint'"):
        eo._resolve_endpoint("otlp", {})


def test_a_push_reports_its_status(monkeypatch, tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")
    pushed: dict = {}

    def fake_push(**kw):
        pushed.update(kw)
        return {"status": "ok", "spans": 3}

    monkeypatch.setattr("mas.lab.telemetry.otlp_push.push_file", fake_push)
    step = eo.ExportOtelStep(name="x", config={"destination": "otlp", "events_jsonl": "events.jsonl", "dry_run": True})
    ctx = SimpleNamespace(output_dir=tmp_path, template_vars={})
    with hostenv.overlay({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}):
        out = asyncio.run(step.execute(ctx))
    assert pushed["endpoint"] == "http://collector:4318" and pushed["dry_run"] is True
    assert out.metadata["status"] == "ok" and out.metadata["spans"] == 3
