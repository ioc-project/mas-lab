#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed data a UI needs to assemble a pipeline. No UI code lives here.

Six building blocks — generator, scheduler, executor, retrier, analyzer, pipeline — each
expose a small API. This catalog is the pipeline's: step types with typed
inputs and outputs, stage modes, and the allowed edges between them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from mas.lab.benchmark.execution.completeness import (
    STAGE_BATCH,
    STAGE_MODES,
    edge_rule,
    normalize_stage_mode,
)
from mas.lab.benchmark.pipeline.core import get_step, list_steps
from mas.lab.benchmark.pipeline.incremental import folds_batches
from mas.runtime.registry import get_registry

ALLOWED_EDGES: tuple[tuple[str, str, str], ...] = tuple(
    (producer, consumer, edge_rule(producer, consumer))
    for producer in sorted(STAGE_MODES)
    for consumer in sorted(STAGE_MODES)
)


@dataclass(frozen=True)
class AssemblyPort:
    name: str
    kind: str = "any"
    description: str = ""


@dataclass(frozen=True)
class AssemblyStep:
    type: str
    category: str
    default_mode: str
    inputs: tuple[AssemblyPort, ...] = ()
    outputs: tuple[AssemblyPort, ...] = ()
    params: tuple[dict[str, Any], ...] = ()
    folds: bool = False
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["inputs"] = [asdict(p) for p in self.inputs]
        data["outputs"] = [asdict(p) for p in self.outputs]
        return data


@dataclass(frozen=True)
class AssemblyCatalog:
    """Machine-readable pipeline assembly surface."""

    blocks: tuple[str, ...] = ("generator", "scheduler", "executor", "retrier", "analyzer", "pipeline")
    stage_modes: tuple[str, ...] = tuple(sorted(STAGE_MODES))
    allowed_edges: tuple[tuple[str, str, str], ...] = ALLOWED_EDGES
    steps: tuple[AssemblyStep, ...] = ()
    lab_api: tuple[str, ...] = ("schedule_batch", "batch_status", "stop_batch", "next_runs")

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocks": list(self.blocks),
            "stage_modes": list(self.stage_modes),
            "allowed_edges": [{"from": src, "to": dst, "when": when} for src, dst, when in self.allowed_edges],
            "lab_api": list(self.lab_api),
            "steps": [step.as_dict() for step in self.steps],
        }


def _ports_from_step(cls: Any, names: tuple[str, ...], *, fallback: tuple[str, ...]) -> tuple[AssemblyPort, ...]:
    declared = tuple(str(n) for n in names if n)
    if not declared:
        declared = fallback
    kind = "artifact"
    return tuple(AssemblyPort(name=name, kind=kind) for name in declared)


def _params_of(cls: Any) -> tuple[dict[str, Any], ...]:
    rows = []
    for param in getattr(cls, "PARAMS", None) or []:
        rows.append(
            {
                "name": param.name,
                "type": getattr(param.type, "__name__", str(param.type)),
                "required": bool(param.required),
                "default": None if param.required else param.default,
                "description": param.description,
            }
        )
    return tuple(rows)


def _step_description(cls: Any, attributes: dict[str, Any]) -> str:
    if attributes.get("description"):
        return str(attributes["description"])
    doc = getattr(cls, "__doc__", None) if cls is not None else None
    if not doc:
        return ""
    return str(doc).strip().splitlines()[0]


def _default_mode(cls: Any, attributes: dict[str, Any]) -> str:
    raw = attributes.get("stage_mode") or attributes.get("mode")
    if raw:
        return normalize_stage_mode(str(raw), realtime=None, folds=False)
    class_mode = getattr(cls, "mode", None) if cls is not None else None
    if isinstance(class_mode, str) and class_mode.strip():
        return normalize_stage_mode(class_mode, realtime=None, folds=False)
    if cls is None:
        return STAGE_BATCH
    try:
        dummy = cls("catalog", {})
        dummy.realtime = None
        return normalize_stage_mode(None, realtime=None, folds=folds_batches(dummy))
    except Exception:
        return STAGE_BATCH


def catalog() -> AssemblyCatalog:
    """Step types, modes, and allowed edges. Safe to JSON-serialise for a UI."""
    registry = get_registry()
    listed = list_steps()
    steps: list[AssemblyStep] = []
    seen: set[str] = set()
    for entry in registry.list():
        if str(entry.get("category") or "") != "step":
            continue
        shortcuts = entry.get("shortcuts") or []
        name = str(shortcuts[0]) if shortcuts else str(entry.get("urn") or "").rsplit(".", 1)[-1]
        if not name or name in seen:
            continue
        seen.add(name)
        attrs = dict(entry.get("attributes") or {})
        cls = listed.get(name)
        if cls is None:
            try:
                cls = get_step(name)
            except Exception:
                cls = None
        ins = tuple(str(x) for x in (attrs.get("inputs") or []))
        out = tuple(str(x) for x in (attrs.get("outputs") or []))
        if cls is not None:
            ins = tuple(getattr(cls, "ins", ()) or ins)
            out = tuple(getattr(cls, "out", ()) or out)
        dummy_folds = False
        if cls is not None:
            try:
                dummy_folds = folds_batches(cls(name, {}))
            except Exception:
                dummy_folds = False
        steps.append(
            AssemblyStep(
                type=name,
                category="step",
                default_mode=_default_mode(cls, attrs),
                inputs=_ports_from_step(cls, ins, fallback=()),
                outputs=_ports_from_step(cls, out, fallback=()),
                params=_params_of(cls) if cls is not None else (),
                folds=dummy_folds,
                description=_step_description(cls, attrs),
            )
        )
    steps.sort(key=lambda item: item.type)
    return AssemblyCatalog(steps=tuple(steps))
