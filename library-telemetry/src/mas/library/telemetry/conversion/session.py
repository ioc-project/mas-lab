#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One OTel export session for live plugin and file replay.

Both the observability plugin and ``replay_events_file`` construct spans
through this factory so realtime vs retrospective ``*.graph`` behavior lives
only on :class:`MasOtelConverter`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.library.telemetry.conversion.exporter import (
    OTEL_AVAILABLE,
    JSONLineFileLogExporter,
    JSONLineFileSpanExporter,
    MultiSpanExporter,
)
from mas.library.telemetry.conversion.layers import ExportLayers, parse_export_layers
from mas.library.telemetry.conversion.profiles import (
    default_extensions,
    normalize_converter_profile,
)
from mas.library.telemetry.exceptions import OtelSdkUnavailableError
from mas.runtime import hostenv


def check_otlp_headers(headers: Any, *, path: str = "otel.headers") -> dict[str, Any]:
    """Validate the OTLP export headers: a mapping of name to plain string or SecretRef.

    A credential header (``Authorization``, ``X-Api-Key``, ``Cookie``, ...; the shared
    :data:`~mas.runtime.secrets.ref.HTTP_SECRET_HEADERS`) must be a SecretRef: a plain string there
    is refused. Returns the map with every ref as a :class:`~mas.runtime.secrets.SecretRef`.
    """
    from mas.runtime.secrets import SecretRefError
    from mas.runtime.secrets.ref import check_http_header_secrets, is_ref, parse_secret_ref

    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise SecretRefError(f"{path}: expected a mapping of header name to string or SecretRef")
    check_http_header_secrets(headers, path=path)
    out: dict[str, Any] = {}
    for name, value in headers.items():
        key = str(name)
        if is_ref(value):
            out[key] = parse_secret_ref(value, field=f"{path}.{key}")
        elif isinstance(value, str):
            out[key] = value
        else:
            raise SecretRefError(f"{path}.{key}: expected a string or a SecretRef, got {type(value).__name__}")
    return out


def _otlp_session(endpoint: str) -> Any:
    """The http session an OTLP exporter sends with. Given explicitly, so the SDK does not load a credential
    provider named by ``OTEL_PYTHON_EXPORTER_OTLP_HTTP_*_CREDENTIAL_PROVIDER``. For a loopback endpoint it
    reads no proxy settings from the environment: the plain-http headers would otherwise go to that proxy.

    The SDK still merges ``OTEL_EXPORTER_OTLP_HEADERS`` and ``OTEL_EXPORTER_OTLP_{TRACES,LOGS}_HEADERS`` from
    the process environment into every export, under the headers given here. The MAS Lab entry points remove
    those variables from the process (:func:`mas.runtime.entrypoint.install_host_environment`); a program that
    embeds this library without an entry point must not set them.
    """
    from mas.runtime.secrets.transport import requests_session_for

    return requests_session_for(endpoint)


def runtime_events_path(spans_path: str | Path) -> Path:
    """Where realtime runtime events (LogRecords) are written next to ``spans_path``."""
    path = Path(spans_path)
    return path.with_name(f"{path.stem}_runtime_events.jsonl")


@dataclass
class OtelExport:
    provider: Any
    converter: Any
    file_exporter: Any
    # Realtime only: the OTel LoggerProvider carrying the runtime events.
    logger_provider: Any = None

    def process_event(self, event: dict[str, Any]) -> None:
        self.converter.process_event(event)

    def finish(self, *, emit_graph: bool | None = None, flush_timeout_ms: int = 5000) -> None:
        self.converter.finish(emit_graph=emit_graph)
        self.provider.force_flush(timeout_millis=flush_timeout_ms)
        if self.logger_provider is not None:
            self.logger_provider.force_flush(timeout_millis=flush_timeout_ms)

    def close(self, *, emit_graph: bool | None = None, flush_timeout_ms: int = 5000) -> None:
        self.finish(emit_graph=emit_graph, flush_timeout_ms=flush_timeout_ms)
        self.provider.shutdown()
        if self.logger_provider is not None:
            self.logger_provider.shutdown()


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
    otlp_headers: Mapping[str, Any] | None = None,
    id_generator: Any | None = None,
    annotation_enabled: bool | None = None,
    timestamp_offset_s: float = 0.0,
    session_uuid: str | None = None,
    agent_llm_models: dict[str, str] | None = None,
    runtime_logs: bool = False,
) -> OtelExport:
    """Stand up a TracerProvider + converter writing JSONL (and optional OTLP).

    With ``realtime``, a LoggerProvider also carries the ioa-observe runtime
    events (``topology.node.*``, ``tool.*``, ``llm.*``) as LogRecords, written
    to :func:`runtime_events_path` and, with an OTLP endpoint, to ``/v1/logs``.
    ``runtime_logs`` stands up the same LoggerProvider for transport aliases
    (observe-sdk names SCOPE already reads) without turning kernel realtime on.

    ``otlp_headers`` maps a header name to a plain string or a SecretRef (see
    :func:`check_otlp_headers`). The refs are revealed once here, for the OTLP exporters this
    call builds, and the values go to those exporters only: a later build reveals again, so a
    rotated secret reaches the next export session. A secret header goes only over https or to a
    loopback endpoint.
    """
    if not OTEL_AVAILABLE:
        raise OtelSdkUnavailableError("required for OTel export")

    from mas.library.telemetry.conversion.converter import MasOtelConverter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator

    class _SharedTraceIdGenerator(IdGenerator):
        """Reuse one TraceId for parentless observe-sdk roots (graph/session/invoke)."""

        def __init__(self, inner: IdGenerator) -> None:
            self._inner = inner
            self._trace_id: int | None = None

        def generate_span_id(self) -> int:
            return self._inner.generate_span_id()

        def generate_trace_id(self) -> int:
            if self._trace_id is None:
                self._trace_id = self._inner.generate_trace_id()
            return self._trace_id

    layers = (
        export_layers
        if isinstance(export_layers, ExportLayers)
        else parse_export_layers(export_layers if isinstance(export_layers, dict) else None)
    )
    profile = normalize_converter_profile(converter_profile if converter_profile is not None else "observe_sdk")
    if annotation_enabled is None:
        # Observe-sdk default matches noa-trip-planner: no CallAnnotation
        # spans (routing, state_update, user_response, …).
        annotation_enabled = profile != "observe_sdk"
    extensions = default_extensions(profile, extensions)

    file_exporter = JSONLineFileSpanExporter(spans_path)
    exporters: list = [file_exporter]
    if not otlp_endpoint:
        otlp_endpoint = hostenv.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip() or None
    # One reveal per build; the values live in this frame and in the exporters, nowhere else.
    headers = None
    if otlp_endpoint:
        from mas.runtime.secrets.transport import reveal_headers_for
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        revealed = reveal_headers_for(otlp_endpoint, check_otlp_headers(otlp_headers), what="OTLP secret headers")
        headers = revealed or None
        exporters.append(
            OTLPSpanExporter(
                endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces",
                headers=headers,
                session=_otlp_session(otlp_endpoint),
            )
        )

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
    logger_provider = None
    if realtime or runtime_logs:
        if realtime:
            try:
                import ioa_observe.sdk.tracing.runtime_events  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    "realtime runtime events are the ioa-observe SDK's: "
                    'pip install "mas-lab-library-telemetry[observe]"'
                ) from exc
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            SimpleLogRecordProcessor(JSONLineFileLogExporter(runtime_events_path(spans_path)))
        )
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

            logger_provider.add_log_record_processor(
                SimpleLogRecordProcessor(
                    OTLPLogExporter(
                        endpoint=f"{otlp_endpoint.rstrip('/')}/v1/logs",
                        headers=headers,
                        session=_otlp_session(otlp_endpoint),
                    )
                )
            )
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
        agent_llm_models=agent_llm_models,
        event_logger=(logger_provider.get_logger("ioa_observe.runtime_events") if logger_provider else None),
    )
    return OtelExport(
        provider=provider,
        converter=converter,
        file_exporter=file_exporter,
        logger_provider=logger_provider,
    )


__all__ = ["OtelExport", "check_otlp_headers", "create_otel_export", "runtime_events_path"]
