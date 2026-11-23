#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pure spec parsers for agent observability — runtime-owned, no ctl dependency."""

from __future__ import annotations

from typing import Any

from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.transport.config import TransportObservabilityConfig


class SpecBindingError(ValueError):
    """Agent spec observability binding violates v2 contract."""


def _normalize_obs_plugin(name: str) -> str:
    """Return trimmed plugin id — hyphens normalized to underscores."""
    return (name or "").strip().replace("-", "_")


def _resolve_manifest_cfg_value(cfg: dict[str, Any], key: str, *, default: str = "") -> str:
    """Return the inline manifest config value."""
    direct = cfg.get(key)
    if direct is not None and str(direct).strip():
        return str(direct).strip()
    return default


def _resolve_path_cfg(cfg: dict[str, Any]) -> str | None:
    """Resolve first non-empty path-like field (inline or env) from plugin config."""
    for key in ("path", "output_path", "events_file", "file_export_path"):
        val = _resolve_manifest_cfg_value(cfg, key)
        if val:
            return val
    return None


def _parse_obs_list(
    items: list[Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    plugins: list[str] = []
    configs: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, str):
            plugins.append(_normalize_obs_plugin(item))
        elif isinstance(item, dict):
            for raw_name, cfg in item.items():
                name = _normalize_obs_plugin(str(raw_name))
                plugins.append(name)
                if isinstance(cfg, dict):
                    configs[name] = dict(cfg)
        else:
            raise SpecBindingError(f"observability list entries must be str or dict, got {type(item).__name__}")
    return plugins, configs


def parse_obs_spec(raw: list | None) -> ObservabilityBinding:
    """Parse ``spec.observability`` — must be a list or absent."""
    if raw is None:
        return ObservabilityBinding(plugins=[])

    if not isinstance(raw, list):
        raise SpecBindingError(f"spec.observability must be a list, got {type(raw).__name__}")

    plugins, configs = _parse_obs_list(raw)
    if any(name == "transport" or name.endswith(".transport") for name in plugins):
        raise SpecBindingError(
            "transport is not an observability plugin; set capture on native.transport and projection on otel.transport"
        )

    events_file: str | None = None
    otlp_endpoint: str | None = None
    for cfg in configs.values():
        path = _resolve_path_cfg(cfg)
        if path and not events_file:
            events_file = path
        if cfg.get("otlp_endpoint") and not otlp_endpoint:
            otlp_endpoint = str(cfg["otlp_endpoint"])

    return ObservabilityBinding(
        plugins=plugins,
        plugin_configs=configs,
        otlp_endpoint=otlp_endpoint,
        events_file=events_file,
        transport=TransportObservabilityConfig.from_plugins(plugins, configs),
    )


__all__ = ["ObservabilityBinding", "SpecBindingError", "parse_obs_spec"]
