#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One OTel export session for live plugin and file replay.

Both the observability plugin and ``replay_events_file`` construct spans
through this factory so realtime vs retrospective ``*.graph`` behavior lives
only on :class:`MasOtelConverter`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.library.telemetry.conversion.exporter import (
    OTEL_AVAILABLE,
    JSONLineFileSpanExporter,
    MultiSpanExporter,
)
from mas.library.telemetry.conversion.layers import ExportLayers, parse_export_layers
from mas.library.telemetry.conversion.profiles import (
    default_extensions,
    normalize_converter_profile,
)
from mas.library.telemetry.exceptions import OtelSdkUnavailableError


@dataclass
class OtelExport:
    provider: Any
    converter: Any
    file_exporter: Any

    def process_event(self, event: dict[str, Any]) -> None:
        self.converter.process_event(event)

    def finish(self, *, emit_graph: bool | None = None, flush_timeout_ms: int = 5000) -> None:
        self.converter.finish(emit_graph=emit_graph)
        self.provider.force_flush(timeout_millis=flush_timeout_ms)

    def close(self, *, emit_graph: bool | None = None, flush_timeout_ms: int = 5000) -> None:
        self.finish(emit_graph=emit_graph, flush_timeout_ms=flush_timeout_ms)
        self.provider.shutdown()


def create_otel_export(
    *,
    spans_path: str | Path,
    service_name: str = "",
    app_name: str = "",
    export_layers: ExportLayers | dict[str, Any] | None = None,
    converter_profile: str | None = "observe_sdk",
    realtime: bool = False,
    extensions: bool | None = None,
    otlp_endpoint: str | None = None,
    id_generator: Any | None = None,
    annotation_enabled: bool | None = None,
    timestamp_offset_s: float = 0.0,
    session_uuid: str | None = None,
    rewrite_tool_delegation: bool = True,
    agent_llm_models: dict[str, str] | None = None,
) -> OtelExport:
    """Stand up a TracerProvider + converter writing JSONL (and optional OTLP)."""
    if not OTEL_AVAILABLE:
        raise OtelSdkUnavailableError("required for OTel export")

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator

    from mas.library.telemetry.conversion.converter import MasOtelConverter, SpanIdReuse

    class _SharedTraceIdGenerator(SpanIdReuse, IdGenerator):
        """Reuse one TraceId for parentless observe-sdk roots (graph/session/invoke).

        Also lets a caller force the *next* span id, so a realtime signal's
        "completed" half can reuse its "started" half's id (emit_realtime_
        signal) -- two independently start()/end()'d spans otherwise always
        get two different random ids, leaving norm with no key to link them
        as one logical call.
        """

        def __init__(self, inner: IdGenerator) -> None:
            self._inner = inner
            self._trace_id: int | None = None
            self._next_span_id: int | None = None

        def generate_span_id(self) -> int:
            if self._next_span_id is not None:
                span_id = self._next_span_id
                self._next_span_id = None
                return span_id
            return self._inner.generate_span_id()

        def generate_trace_id(self) -> int:
            if self._trace_id is None:
                self._trace_id = self._inner.generate_trace_id()
            return self._trace_id

        def reuse_span_id(self, span_id: int) -> None:
            self._next_span_id = span_id

    layers = (
        export_layers
        if isinstance(export_layers, ExportLayers)
        else parse_export_layers(export_layers if isinstance(export_layers, dict) else None)
    )
    profile = normalize_converter_profile(
        converter_profile if converter_profile is not None else "observe_sdk"
    )
    if annotation_enabled is None:
        # Observe-sdk default matches noa-trip-planner: no CallAnnotation
        # spans (routing, state_update, user_response, …).
        annotation_enabled = profile != "observe_sdk"
    extensions = default_extensions(profile, extensions)

    file_exporter = JSONLineFileSpanExporter(spans_path)
    exporters: list = [file_exporter]
    if not otlp_endpoint:
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip() or None
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporters.append(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))

    from mas.library.telemetry.conversion.topology import require_mas_name

    # service_name is kept on the signature for call-site compatibility.
    # It is not MAS identity. Observe SDK's resource service.name is the
    # declared MAS name (application_id). Agent names stay on agent_id.
    mas_name = require_mas_name(app_name)
    resource_attrs = {"service.name": mas_name}
    if profile != "observe_sdk":
        resource_attrs["mas.instrumentation.version"] = "1.0.0"
        resource_attrs["mas.plugin"] = "mas.library.telemetry"
    # observe_sdk: noa-trip-planner resource is service.name only.
    # resource_attrs["mas.instrumentation.version"] = "1.0.0"
    # resource_attrs["mas.plugin"] = "mas.library.telemetry"
    resource = Resource.create(resource_attrs)
    kwargs: dict[str, Any] = {"resource": resource}
    generator = id_generator if id_generator is not None else RandomIdGenerator()
    if profile == "observe_sdk":
        generator = _SharedTraceIdGenerator(generator)
    kwargs["id_generator"] = generator
    provider = TracerProvider(**kwargs)
    sink = exporters[0] if len(exporters) == 1 else MultiSpanExporter(exporters)
    provider.add_span_processor(SimpleSpanProcessor(sink))
    converter = MasOtelConverter(
        provider.get_tracer("mas-otel"),
        app_name=mas_name,
        export_layers=layers,
        converter_profile=profile,
        annotation_enabled=annotation_enabled,
        extensions=extensions,
        realtime=realtime,
        timestamp_offset_s=timestamp_offset_s,
        session_uuid=session_uuid,
        rewrite_tool_delegation=rewrite_tool_delegation,
        agent_llm_models=agent_llm_models,
        id_generator=generator,
    )
    return OtelExport(provider=provider, converter=converter, file_exporter=file_exporter)


__all__ = ["OtelExport", "create_otel_export"]
