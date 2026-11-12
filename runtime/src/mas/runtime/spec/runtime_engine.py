#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Apply resolved RuntimeEngine manifests to KernelConfig."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from mas.runtime.kernel.config import KernelConfig


def merge_runtime_engine_layers(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge normalized RuntimeEngine layers (later layers win scalars; nested cache merge)."""
    out: dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        for key, val in layer.items():
            if key == "cache" and isinstance(val, dict):
                prev = out.get(key)
                out[key] = {**(prev if isinstance(prev, dict) else {}), **val}
            else:
                out[key] = val
    return out


def normalize_runtime_engine_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Flatten infra/v1 RuntimeEngine ``spec`` for kernel application."""
    out: dict[str, Any] = {}
    engine = spec.get("engine") or {}
    if isinstance(engine, dict):
        if "queue_depth" in engine:
            out["engine_queue_depth"] = engine["queue_depth"]
        if "max_auto_steps" in engine:
            out["max_auto_steps"] = engine["max_auto_steps"]
        if engine.get("timeout") is not None:
            out["timeout"] = engine["timeout"]
        if "tool_worker" in engine:
            out["tool_worker"] = engine["tool_worker"]
        if "tool_concurrency" in engine:
            out["tool_concurrency"] = engine["tool_concurrency"]
    for key in ("stream", "parallel", "cache"):
        if key in spec:
            out[key] = spec[key]
    return out


def apply_runtime_engine_to_kernel(
    kernel: KernelConfig,
    runtime_engine: dict[str, Any] | None,
) -> KernelConfig:
    """Apply merged RuntimeEngine fields to kernel config."""
    if not runtime_engine:
        return kernel
    cfg = kernel
    if "engine_queue_depth" in runtime_engine:
        cfg = replace(cfg, engine_queue_depth=int(runtime_engine["engine_queue_depth"]))
    if "max_auto_steps" in runtime_engine:
        raw = runtime_engine["max_auto_steps"]
        cfg = replace(cfg, max_auto_steps=None if raw is None else int(raw))
    if "tool_worker" in runtime_engine:
        cfg = replace(cfg, tool_worker=str(runtime_engine["tool_worker"]))
    if "tool_concurrency" in runtime_engine:
        raw = runtime_engine["tool_concurrency"]
        cfg = replace(cfg, tool_concurrency=None if raw is None else int(raw))
    if "parallel" in runtime_engine:
        cfg = replace(cfg, parallel_tool_calls=bool(runtime_engine["parallel"]))
    return cfg
