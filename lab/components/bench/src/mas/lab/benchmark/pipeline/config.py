#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Declarative stream-engine settings (no CLI flag)."""

from __future__ import annotations

from typing import Any

from mas.lab.lab.config.execution import PipelineEngineSpec

__all__ = ["PipelineEngineSpec", "pipeline_settings"]


def pipeline_settings(exp: Any | None = None, extra: dict[str, Any] | None = None) -> PipelineEngineSpec:
    if extra:
        data: dict[str, Any] = {}
        if extra.get("pipeline_store"):
            data["store"] = extra["pipeline_store"]
        if extra.get("pipeline_transport"):
            data["transport"] = extra["pipeline_transport"]
        if extra.get("store"):
            data["store"] = extra["store"]
        return PipelineEngineSpec.from_dict(data)
    emu = getattr(exp, "bench_emulation", None) if exp is not None else None
    pipe = getattr(emu, "pipeline", None) if emu is not None else None
    if isinstance(pipe, PipelineEngineSpec):
        return pipe
    if isinstance(pipe, dict):
        return PipelineEngineSpec.from_dict(pipe)
    return PipelineEngineSpec()
