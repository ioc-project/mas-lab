#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ObservabilityBinding — frozen parsed spec struct flowing from manifest → runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ObservabilityBinding:
    """Parsed ``spec.observability`` — passed from ctl into RuntimeInstance.

    All fields have safe defaults so callers can construct a minimal binding
    with just the fields they care about.
    """

    # Ordered plugin names from the registry
    plugins: list[str] = field(default_factory=list)
    # Per-plugin keyword config dicts, keyed by plugin name
    plugin_configs: dict[str, dict] = field(default_factory=dict)
    # Events file path (relative or absolute string; runtime resolves against base_dir)
    events_file: str | None = None
    # Optional env-var name for a plugin-specific collector URL
    otlp_endpoint_env: str | None = None
    # Whether to include message content in traces
    trace_content: bool = True
    # Emit events to stdout as well as file
    stdout: bool = False

    def with_mas_id(self, mas_id: str) -> "ObservabilityBinding":
        """Stamp the MAS name onto native/otel plugin configs when blank."""
        name = str(mas_id or "").strip()
        if not name:
            return self
        if "/" in name or "\\" in name:
            raise ValueError(
                "MAS app name must be a declared identity, not a filesystem path: "
                f"{name!r}"
            )
        configs = {key: dict(value) for key, value in self.plugin_configs.items()}
        for plugin in self.plugins:
            cfg = dict(configs.get(plugin) or {})
            if not str(cfg.get("app_name") or "").strip():
                cfg["app_name"] = name
            if not str(cfg.get("mas_id") or "").strip():
                cfg["mas_id"] = name
            configs[plugin] = cfg
        return replace(self, plugin_configs=configs)


__all__ = ["ObservabilityBinding"]
