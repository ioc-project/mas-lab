#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime plugins shipped by the telemetry library."""

from __future__ import annotations

from typing import Any

__all__ = ["OtelObservabilityPlugin"]


def __getattr__(name: str) -> Any:
    if name == "OtelObservabilityPlugin":
        from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

        return OtelObservabilityPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
