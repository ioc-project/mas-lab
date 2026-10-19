#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OtelObservabilityPlugin — live native-event export via the shared OTel session.

Default converter profile is ``observe_sdk``. ``otel.realtime`` (default false)
opts into incremental topology/tool/llm signals; when it is on, the
end-of-run ``.graph`` span is not also emitted (realtime replaces it).

Live OTLP does not need an infra ``OtelCollector`` manifest: add the ``otel``
plugin on the spec and set ``$OTEL_EXPORTER_OTLP_ENDPOINT``.
``otlp_endpoint_env`` / ``binding.otlp_endpoint_env`` override that name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.library.standard.lib.observability.native.emit_transition import project_transition
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.telemetry.conversion.layers import ExportLayers, parse_export_layers
from mas.library.telemetry.conversion.session import OtelExport, create_otel_export
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.transition import TransitionEvent

_TRUE = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
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
    export_layers: ExportLayers | None = None,
    converter_profile: str | None = "observe_sdk",
    realtime: bool = False,
    extensions: bool | None = None,
    rewrite_tool_delegation: bool = True,
) -> OtelObservabilityPlugin:
    from mas.library.telemetry.conversion.topology import require_mas_name

    mas_name = require_mas_name(app_name, mas_id)
    export = create_otel_export(
        spans_path=spans_path,
        service_name=mas_name,
        app_name=mas_name,
        otlp_endpoint=otlp_endpoint,
        export_layers=export_layers,
        converter_profile=converter_profile,
        realtime=realtime,
        extensions=extensions,
        rewrite_tool_delegation=rewrite_tool_delegation,
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

    def flush(self) -> None:
        if self._export is not None:
            self._export.finish()
        elif self.converter is not None:
            self.converter.finish()

    def close(self) -> None:
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
        otel_cfg = binding.plugin_configs.get("otel") or {}
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

        env_name = str(
            otel_cfg.get("otlp_endpoint_env")
            or binding.otlp_endpoint_env
            or "OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        endpoint = os.environ.get(env_name, "").strip() or None

        from mas.library.telemetry.conversion.topology import require_mas_name

        mas_name = require_mas_name(
            str(otel_cfg.get("app_name") or ""),
            str(otel_cfg.get("mas_id") or ""),
        )
        profile = str(otel_cfg.get("converter_profile") or "observe_sdk")
        realtime = bool(otel_cfg.get("realtime", False)) or _env_flag(
            "OBSERVE_REALTIME_OBSERVABILITY_ENABLED", False
        )
        ext_cfg = otel_cfg.get("extensions")
        rewrite_cfg = otel_cfg.get("rewrite_tool_delegation")
        ctx = TransformContext(agent_id=agent_id, run_id="")
        return create_otel_plugin(
            spans_path=spans_path,
            context=ctx,
            mas_id=mas_name,
            service_name=mas_name,
            app_name=mas_name,
            otlp_endpoint=endpoint,
            export_layers=parse_export_layers(otel_cfg),
            converter_profile=profile,
            realtime=realtime,
            extensions=None if ext_cfg is None else bool(ext_cfg),
            rewrite_tool_delegation=True if rewrite_cfg is None else bool(rewrite_cfg),
        )


__all__ = ["OtelObservabilityPlugin", "create_otel_plugin"]
