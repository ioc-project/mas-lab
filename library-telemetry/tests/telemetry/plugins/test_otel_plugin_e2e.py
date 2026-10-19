#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""End-to-end coverage for OtelObservabilityPlugin with the shared OTel session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas.library.telemetry.conversion.exporter import OTEL_AVAILABLE
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.transition import TransitionEvent

pytestmark = pytest.mark.skipif(not OTEL_AVAILABLE, reason="opentelemetry-sdk not installed")


def _otlp_exporter_available() -> bool:
    try:
        import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401
        return True
    except Exception:
        return False


_needs_otlp = pytest.mark.skipif(
    not _otlp_exporter_available(), reason="opentelemetry OTLP http exporter not installed"
)

_RECORDS = [
    {"kind": "system_specification", "timestamp": 1.0,
     "agents": [{"id": "planner"}, {"id": "worker"}], "app_name": "sample-app"},
    {"kind": "mas_call_start", "call_id": "mas-1", "timestamp": 1.0,
     "agent_id": "planner", "run_id": "r1", "session_id": "s1"},
    {"kind": "routing", "timestamp": 2.0, "source_agent_id": "planner",
     "target_agent_id": "worker", "call_id": "mas-1"},
    {"kind": "mas_call_end", "call_id": "mas-1", "status": "success", "timestamp": 3.0},
]


def _patch_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        "mas.library.telemetry.plugins.otel_plugin.project_transition",
        lambda *_a, **_k: _RECORDS,
    )


def _transition() -> TransitionEvent:
    return TransitionEvent(
        contract_id="orchestrator", mealy_symbol="user_input", phase="event",
        agent_id="planner", run_id="r1", boundary_kind="session",
    )


def test_create_otel_plugin_full_lifecycle(tmp_path: Path, monkeypatch) -> None:
    from mas.library.standard.lib.observability.native.transform import TransformContext
    from mas.library.telemetry.plugins.otel_plugin import create_otel_plugin

    _patch_projection(monkeypatch)
    spans_path = tmp_path / "traces" / "otel_sdk_spans.jsonl"
    plugin = create_otel_plugin(
        spans_path=spans_path,
        context=TransformContext(agent_id="planner", run_id="r1"),
        app_name="sample-app",
    )
    plugin.on_transition(_transition())
    plugin.flush()
    blob = spans_path.read_text()
    assert "sample-app.graph" in blob
    assert "gen_ai.ioa.graph" in blob
    spans = [json.loads(line) for line in blob.splitlines() if line.strip()]
    assert sum(1 for span in spans if span.get("name") == "session.end") == 1
    graph = next(span for span in spans if str(span.get("name") or "").endswith(".graph"))
    payload = json.loads((graph.get("attributes") or {})["gen_ai.ioa.graph"])
    assert isinstance(payload.get("nodes"), dict)

    plugin.reset_run()
    assert plugin.converter is not None
    assert plugin.converter._seen_events == []
    assert plugin.converter._graph_emitted is False

    plugin.close()


@_needs_otlp
def test_create_otel_export_with_otlp_endpoint(tmp_path: Path) -> None:
    from mas.library.telemetry.conversion.session import create_otel_export

    export = create_otel_export(
        spans_path=tmp_path / "s.jsonl",
        app_name="sample-app",
        otlp_endpoint="http://localhost:4318/",
    )
    assert export.converter is not None
    export.provider.shutdown()


def test_from_binding_constructs_plugin(tmp_path: Path, monkeypatch) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    _patch_projection(monkeypatch)
    events_file = tmp_path / "traces" / "events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text("", encoding="utf-8")

    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={"otel": {"service_name": "svc", "app_name": "sample-app"}},
        events_file=str(events_file),
    )
    plugin = OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")
    assert plugin is not None
    assert plugin.spans_path == (tmp_path / "traces" / "otel_sdk_spans.jsonl")

    plugin.on_transition(_transition())
    plugin.close()
    assert (tmp_path / "traces" / "otel_sdk_spans.jsonl").read_text()
    assert plugin.mas_id == "sample-app"
    assert plugin.converter is not None
    assert plugin.converter._app_name == "sample-app"


def test_from_binding_requires_declared_mas_name(tmp_path: Path) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {
                "output_path": str(tmp_path / "out.jsonl"),
                "converter_profile": "observe_sdk",
            }
        },
    )
    with pytest.raises(ValueError, match="MAS app name is required"):
        OtelObservabilityPlugin.from_binding(
            binding, base_dir=tmp_path, agent_id="qa-agent"
        )


def test_from_binding_rejects_filesystem_path_app_name(tmp_path: Path) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {
                "output_path": str(tmp_path / "out.jsonl"),
                "app_name": "/opt/mas-lab/library-samples/apps/trip-planner/agents",
            }
        },
    )
    with pytest.raises(ValueError, match="filesystem path"):
        OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")


def test_from_binding_service_name_is_not_mas_identity(tmp_path: Path) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {
                "output_path": str(tmp_path / "out.jsonl"),
                "service_name": "mas-runtime",
            }
        },
    )
    with pytest.raises(ValueError, match="MAS app name is required"):
        OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")


def test_from_binding_uses_mas_id_not_agent_or_runtime(tmp_path: Path) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {
                "output_path": str(tmp_path / "out.jsonl"),
                "service_name": "mas-runtime",
                "mas_id": "sample-app",
            }
        },
    )
    plugin = OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")
    assert plugin is not None
    assert plugin.mas_id == "sample-app"
    assert plugin.converter is not None
    assert plugin.converter._app_name == "sample-app"
    plugin.close()


@_needs_otlp
def test_from_binding_honours_env_otlp_endpoint(tmp_path: Path, monkeypatch) -> None:
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    monkeypatch.setenv("MY_OTLP", "http://localhost:4318")
    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {"output_path": str(tmp_path / "out.jsonl"), "app_name": "sample-app"}
        },
        otlp_endpoint_env="MY_OTLP",
    )
    plugin = OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="a")
    assert plugin is not None
    assert plugin.spans_path == (tmp_path / "out.jsonl")
    plugin.close()


@_needs_otlp
def test_from_binding_defaults_to_otel_exporter_env(tmp_path: Path, monkeypatch) -> None:
    """Spec plugin + ``$OTEL_EXPORTER_OTLP_ENDPOINT`` is enough — no infra YAML."""
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={
            "otel": {"output_path": str(tmp_path / "out.jsonl"), "app_name": "sample-app"}
        },
    )
    plugin = OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="a")
    assert plugin is not None
    plugin.close()
