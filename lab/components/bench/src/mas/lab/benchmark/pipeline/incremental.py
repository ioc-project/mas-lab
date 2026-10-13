#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""IncrementalStep protocol and helpers for run-scope A-class folds."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.keys import RunKey


@runtime_checkable
class IncrementalStep(Protocol):
    VERSION: str
    resource: str

    def begin(self, key: RunKey, ctx: Any) -> None: ...

    def on_batch(self, batch: list[dict[str, Any]], ctx: Any) -> None: ...

    def snapshot(self) -> bytes: ...

    def restore(self, blob: bytes) -> None: ...

    async def finish(self, ctx: Any) -> StepOutput: ...


def declared_callable(step: object, name: str):
    """Return *name* if the step's class (not PipelineStep) defines it."""
    for cls in type(step).mro():
        if cls is object:
            break
        found = cls.__dict__.get(name)
        if callable(found):
            from mas.lab.benchmark.pipeline.core import PipelineStep

            if cls is PipelineStep:
                return None
            return found
    return None


def folds_batches(step: object) -> bool:
    """True when the step class implements ``on_batch`` (live or sealed fold)."""
    return declared_callable(step, "on_batch") is not None


def step_version(step: object) -> str:
    version = type(step).__dict__.get("VERSION")
    if version:
        return str(version)
    try:
        import hashlib
        import inspect

        src = inspect.getsource(type(step))
        return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "0"


def default_on_batch(step: Any, batch: list[dict[str, Any]], ctx: Any) -> None:
    on_batch = declared_callable(step, "on_batch")
    if on_batch is not None:
        on_batch(step, batch, ctx)
        return
    step.on_batch(batch, ctx)


class IncrementalMixin:
    """Per-run keyed fold state. Mix into a PipelineStep."""

    VERSION: str = "1"
    resource: str = "cpu"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._folds: dict[str, Any] = {}
        self._fold_offset: dict[str, int] = {}
        self._fold_digest: dict[str, str] = {}

    def begin(self, key: RunKey, ctx: Any) -> None:
        self._folds[key.stream_id()] = {}
        self._fold_offset[key.stream_id()] = 0
        self._fold_digest[key.stream_id()] = ""

    def snapshot(self) -> bytes:
        return json.dumps(
            {
                "folds": self._folds,
                "offset": self._fold_offset,
                "digest": self._fold_digest,
            },
            default=str,
        ).encode("utf-8")

    def restore(self, blob: bytes) -> None:
        data = json.loads(blob.decode("utf-8"))
        self._folds = data.get("folds") or {}
        self._fold_offset = {k: int(v) for k, v in (data.get("offset") or {}).items()}
        self._fold_digest = data.get("digest") or {}

    async def finish(self, ctx: Any) -> StepOutput:
        del ctx
        n = sum(len(v) if isinstance(v, (list, dict)) else 1 for v in self._folds.values())
        return StepOutput(
            data={"folds": dict(self._folds), "records": n},
            metadata={"from_fold": True, "streams": list(self._folds)},
        )


class ProgressiveStep(Protocol):
    def partial(self, ctx: Any, children: list[Any]) -> list[dict]: ...
