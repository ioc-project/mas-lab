#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ObservabilityConfig — manifest-resolved observability settings."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool = False
    format: str = "native"
    events_file: str | None = None
    events_stdout: bool = False
    otel_file: str | None = None
    sink_ref: str | None = None
    agent_id: str = ""
    mas_id: str = ""
    plugins: list[str] = field(default_factory=list)
    plugin_configs: dict[str, dict] = field(default_factory=dict)


__all__ = ["ObservabilityConfig"]
