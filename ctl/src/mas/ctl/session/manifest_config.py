#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve kernel + observability settings from merged manifests (spec-first)."""

from __future__ import annotations

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.manifest.spec_bindings import (
    normalize_obs_plugin,
    parse_governance,
    parse_observability,
    parse_sink_from_deployment,
)
from mas.runtime.kernel.config import KernelConfig


# DEPRECATED: kernel_config_from_manifest is superseded by RuntimeInstance.from_spec()
# (runtime/src/mas/runtime/driver/instance.py). New callers should use from_spec() directly.
# This function is retained for backward compatibility and will be removed in a future release.
def kernel_config_from_manifest(
    manifest: dict | None,
    *,
    pattern_plugin_id: str | None = None,
) -> KernelConfig:
    from mas.runtime.spec.gov import build_kernel_config

    spec = (manifest or {}).get("spec") or {}
    gov = parse_governance(spec.get("governance"))

    # Ingress plugins are wired inside build_kernel_config from
    # binding.ingress_plugins. Do not pass a second spec list here — that
    # parameter was dead and looked like a double-wiring path.
    return build_kernel_config(gov, pattern_plugin_id=pattern_plugin_id or "", agent_spec=spec)


def agent_id_from_manifest(manifest: dict | None) -> str:
    """``metadata.name`` on the loaded Agent or MAS document (schema field)."""
    meta = (manifest or {}).get("metadata") or {}
    return str(meta.get("name") or "").strip()


def require_agent_id_from_manifest(manifest: dict | None) -> str:
    """``metadata.name``, required when the loaded document is an Agent or MAS."""
    name = agent_id_from_manifest(manifest)
    kind = str((manifest or {}).get("kind") or "").strip().lower()
    if kind in {"agent", "mas"}:
        if not name:
            raise ValueError("metadata.name is required on the loaded manifest")
        if "/" in name or "\\" in name:
            raise ValueError(
                "metadata.name must be a declared identity, not a filesystem path: "
                f"{name!r}"
            )
    return name


def mas_id_from_manifest(manifest: dict | None) -> str:
    """MAS identity is this document's ``metadata.name``.

    Both ``kind: MAS`` and ``kind: Agent`` declare identity only as
    ``metadata.name``. There is no ``metadata.mas_id`` or nested ``mas.name``
    in the schemas.
    """
    return agent_id_from_manifest(manifest)


def resolve_mas_id(manifest: dict | None, *, mas_id: str = "") -> str:
    """Explicit override, else ``metadata.name`` on *manifest*."""
    return mas_id or mas_id_from_manifest(manifest)


def derive_observability_format(
    plugins: list[str],
    *,
    cli_override: str | None = None,
) -> str:
    """Derive export format from manifest plugin list and optional CLI override."""
    if cli_override:
        return cli_override
    has_native = "native" in plugins
    has_otel = "otel" in plugins
    if has_native and has_otel:
        return "both"
    if has_otel:
        return "otel"
    return "native"


def observability_config_from_manifest(
    manifest: dict | None,
    *,
    deployment: dict | None = None,
    agent_id: str = "",
    mas_id: str = "",
    cli_events: bool | None = None,
    cli_events_file: str | None = None,
    cli_events_stdout: bool = False,
    cli_events_format: str | None = None,
    flavour_spec: dict | None = None,
) -> ObservabilityConfig:
    """Spec / deployment first; CLI flags override when explicitly set.

    ``flavour_spec`` (the active flavour's ``spec``, from
    ``mas.ctl.session.flavour.resolve_flavour``) supplies the plugin-selection
    fallback when the agent/MAS manifest doesn't declare its own
    ``spec.observability`` — a flavour-level default (e.g. ``local`` →
    ``[native]``) rather than an agent-authored override. An agent that
    declares its own ``spec.observability`` still wins (per-agent scoping).
    """
    spec = (manifest or {}).get("spec") or {}
    binding = parse_observability(spec.get("observability"))
    if not binding.plugins and flavour_spec:
        binding = parse_observability(flavour_spec.get("observability"))
    sink_ref = parse_sink_from_deployment(deployment)

    plugins = list(binding.plugins)
    if sink_ref and not plugins:
        plugins = [normalize_obs_plugin(sink_ref)]

    enabled = bool(plugins) or bool(sink_ref)
    if cli_events is not None:
        enabled = cli_events

    fmt = derive_observability_format(plugins, cli_override=cli_events_format)

    otel_cfg = binding.plugin_configs.get("otel") or {}
    otel_file = otel_cfg.get("otel_file")
    if not otel_file and otel_cfg.get("output_path"):
        from pathlib import Path

        otel_file = str(Path(str(otel_cfg["output_path"])) / "otel_sdk_spans.jsonl")

    events_file = cli_events_file or binding.events_file
    if not events_file:
        native_cfg = binding.plugin_configs.get("native") or {}
        events_file = native_cfg.get("path") or native_cfg.get("events_file")

    events_stdout = cli_events_stdout or binding.stdout

    return ObservabilityConfig(
        enabled=bool(enabled),
        format=str(fmt),
        events_file=str(events_file) if events_file else None,
        events_stdout=events_stdout,
        otel_file=str(otel_file) if otel_file else None,
        sink_ref=str(sink_ref) if sink_ref else None,
        agent_id=agent_id,
        mas_id=resolve_mas_id(manifest, mas_id=mas_id),
        plugins=plugins,
        plugin_configs=dict(binding.plugin_configs),
    )


def engine_use_tool_loop(manifest: dict | None, kernel: KernelConfig) -> bool:
    """Whether the LLM engine exposes tools for native function-calling.

    Structural only — not a manifest flag. ReAct with declared tools or skills
    ⇒ tool egress in the Mealy product (``activate_skill`` is a system tool
    when ``spec.skills`` is non-empty). Plan-execute schedules tools via the
    DP plugin instead.
    """
    if kernel.pattern_plugin_id == "plan_execute@v1":
        return False
    spec = (manifest or {}).get("spec") or {}
    return bool(spec.get("tools") or spec.get("skills"))
