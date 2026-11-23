#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OtelObservabilityPlugin — live native-event export via the shared OTel session.

Default converter profile is ``observe_sdk``. ``otel.realtime`` (default false)
opts into incremental topology/tool/llm signals; when it is on, the
end-of-run ``.graph`` span is not also emitted (realtime replaces it).

Live OTLP does not need an infra ``OtelCollector`` manifest: add the ``otel``
plugin on the spec and set ``otlp_endpoint`` (a plain overlay URL). A collector that
wants credentials gets them through ``headers`` (name to string or SecretRef; an auth
header must be a SecretRef), revealed when the exporter is built.

The OpenTelemetry SDK also adds the headers of ``OTEL_EXPORTER_OTLP_HEADERS`` and
``OTEL_EXPORTER_OTLP_{TRACES,LOGS}_HEADERS`` from the process environment to every export; it has no
switch to ignore them. The ``mas-ctl`` and ``mas-lab`` entry points remove them from the process before
any exporter is built. A program that embeds this plugin without those entry points must not set them:
the credential would go to the collector from the environment, outside the secret store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.library.standard.lib.observability.native.emit_transition import project_transition
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.telemetry.conversion.layers import ExportLayers, parse_export_layers
from mas.library.telemetry.conversion.session import OtelExport, check_otlp_headers, create_otel_export
from mas.runtime import hostenv
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.schema.transport import TransportEvent
from mas.runtime.transport.config import OTEL_SINK_NAMES, plugin_cfg
from mas.runtime.transport.emit import bind_sink, protocol_config, unbind_sink

_TRUE = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = hostenv.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def create_otel_plugin(
    *,
    spans_path: Path,
    context: TransformContext,
    mas_id: str = "",
    session_id: str = "",
    service_name: str = "",
    app_name: str = "",
    otlp_endpoint: str | None = None,
    otlp_headers: dict[str, Any] | None = None,
    export_layers: ExportLayers | None = None,
    converter_profile: str | None = "observe_sdk",
    realtime: bool = False,
    extensions: bool | None = None,
    runtime_logs: bool = False,
) -> OtelObservabilityPlugin:
    from mas.library.telemetry.conversion.topology import require_mas_name

    mas_name = require_mas_name(app_name, mas_id)
    export = create_otel_export(
        spans_path=spans_path,
        service_name=mas_name,
        app_name=mas_name,
        otlp_endpoint=otlp_endpoint,
        otlp_headers=otlp_headers,
        export_layers=export_layers,
        converter_profile=converter_profile,
        realtime=realtime,
        extensions=extensions,
        runtime_logs=runtime_logs,
    )
    plugin = OtelObservabilityPlugin(
        converter=export.converter,
        context=context,
        mas_id=mas_name,
        session_id=session_id,
        spans_path=spans_path,
        realtime=realtime,
    )
    plugin._export = export
    return plugin


@dataclass
class OtelObservabilityPlugin(ObservabilityPlugin):
    plugin_id: str = "otel_observability@v1"
    implements = ["observability"]
    converter: Any | None = None
    native_transform: NativeObservabilityTransform = field(default_factory=NativeObservabilityTransform)
    context: TransformContext = field(default_factory=TransformContext)
    mas_id: str = ""
    session_id: str = ""
    spans_path: Path | None = None
    realtime: bool = False
    _export: OtelExport | None = field(default=None, init=False, repr=False)
    _alias_tables: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        bind_sink(self)

    def reset_run(self) -> None:
        if self.converter is not None:
            self.converter.reset_run()
        file_exporter = None if self._export is None else self._export.file_exporter
        if file_exporter is not None and self.spans_path is not None:
            self.spans_path.parent.mkdir(parents=True, exist_ok=True)
            file_exporter.set_path(self.spans_path)
            file_exporter.reset()

    def on_transition(self, event: TransitionEvent) -> None:
        if self.converter is None:
            return
        for rec in project_transition(
            event,
            transforms=[self.native_transform],
            ctx=self.context,
            mas_id=self.mas_id,
            session_id=event.session_id or self.session_id,
            task_id=event.task_id,
        ):
            self.converter.process_event(rec)

    def emit(self, event: TransportEvent) -> None:
        """Transport bus sink: observe-sdk aliases SCOPE's norm already reads."""
        cfg = protocol_config(event.protocol)
        if not cfg.aliases_enabled():
            return
        logger = getattr(self.converter, "_event_logger", None) if self.converter is not None else None
        if logger is None:
            return
        from mas.library.telemetry.transport.projector import project

        result = project(
            [event],
            mode="contract",
            aliases=self._alias_table(event.protocol),
            emit_aliases=True,
        )
        self._emit_aliases(event, result.aliases, logger)

    def _alias_table(self, protocol: str) -> Any:
        cached = self._alias_tables.get(protocol)
        if cached is not None:
            return cached
        from mas.runtime.registry import get_registry
        from mas.runtime.transport.classifier import AliasTable

        table = AliasTable()
        for entry in get_registry().get_by_category("transport_classifier"):
            if entry.urn.rsplit(".", 1)[-1] != protocol:
                continue
            table = entry.resolve().load_class()().aliases()
            break
        if table.events:
            self._alias_tables[protocol] = table
        return table

    def _emit_aliases(self, event: TransportEvent, aliases: list[Any], logger: Any) -> None:
        import time

        from opentelemetry._logs import LogRecord, SeverityNumber

        when = int(float(event.timestamp) * 1e9) if event.timestamp else time.time_ns()
        for alias in aliases:
            attrs = {"event.name": alias.name, **dict(alias.attributes)}
            if event.mas_id and "application_id" not in attrs:
                attrs.setdefault("application_id", event.mas_id)
            logger.emit(
                LogRecord(
                    timestamp=when,
                    observed_timestamp=when,
                    severity_text="INFO",
                    severity_number=SeverityNumber.INFO,
                    body=alias.name,
                    attributes=attrs,
                )
            )

    def flush(self) -> None:
        if self._export is not None:
            self._export.finish()
        elif self.converter is not None:
            self.converter.finish()

    def close(self) -> None:
        unbind_sink(self)
        if self._export is not None:
            self._export.close()
        else:
            self.flush()

    @classmethod
    def from_binding(
        cls,
        binding: ObservabilityBinding,
        *,
        base_dir: str | Path,
        agent_id: str,
    ) -> "OtelObservabilityPlugin" | None:
        otel_cfg = plugin_cfg(binding.plugin_configs, OTEL_SINK_NAMES)
        base_path = Path(base_dir)
        events_path = binding.events_file or str(base_path / "traces" / "events.jsonl")
        out = otel_cfg.get("output_path") or otel_cfg.get("otel_file")
        if out:
            out_path = Path(str(out))
            spans_path_str = str(out_path if out_path.suffix == ".jsonl" else out_path / "otel_sdk_spans.jsonl")
        else:
            spans_path_str = str(Path(events_path).parent / "otel_sdk_spans.jsonl")

        spans_path = Path(spans_path_str)
        if not spans_path.is_absolute():
            spans_path = (base_path / spans_path).resolve()

        endpoint = str(otel_cfg.get("otlp_endpoint") or getattr(binding, "otlp_endpoint", None) or "").strip() or None
        headers = check_otlp_headers(otel_cfg.get("headers"))

        from mas.library.telemetry.conversion.topology import require_mas_name

        mas_name = require_mas_name(
            str(otel_cfg.get("app_name") or ""),
            str(otel_cfg.get("mas_id") or ""),
        )
        profile = str(otel_cfg.get("converter_profile") or "observe_sdk")
        realtime = bool(otel_cfg.get("realtime", False)) or _env_flag("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", False)
        ext_cfg = otel_cfg.get("extensions")
        ctx = TransformContext(agent_id=agent_id, run_id="")
        return create_otel_plugin(
            spans_path=spans_path,
            context=ctx,
            mas_id=mas_name,
            service_name=mas_name,
            app_name=mas_name,
            otlp_endpoint=endpoint,
            otlp_headers=headers,
            export_layers=parse_export_layers(otel_cfg),
            converter_profile=profile,
            realtime=realtime,
            extensions=None if ext_cfg is None else bool(ext_cfg),
            runtime_logs=binding.transport.any_aliases(),
        )


__all__ = ["OtelObservabilityPlugin", "create_otel_plugin"]
