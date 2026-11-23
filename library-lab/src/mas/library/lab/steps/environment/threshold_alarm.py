#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from typing import Any, Mapping

from mas.lab.benchmark.environment.hub import get_environment_hub
from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep
from mas.lab.benchmark.pipeline.core import StepOutput

_OPS = {
    ">": lambda num, value: num > value,
    ">=": lambda num, value: num >= value,
    "<": lambda num, value: num < value,
    "<=": lambda num, value: num <= value,
    "==": lambda num, value: num == value,
}


def _crosses(num: float, op: str, value: float) -> bool:
    try:
        return _OPS[op](num, value)
    except KeyError:
        raise ValueError(f"threshold_alarm op must be one of {sorted(_OPS)}, got {op!r}") from None


class ThresholdAlarmStep(EnvironmentStep):
    type = "threshold_alarm"

    def _emit_name(self) -> str:
        return str(self.config.get("emit") or self.config.get("event") or "user.alarm")

    def _threshold(self) -> tuple[str, str, float]:
        field = str(self.config.get("field") or self.config.get("metric") or "")
        op = str(self.config.get("op") or ">")
        raw = self.config.get("value")
        if raw is None:
            raw = self.config.get("threshold") or 0
        value = float(raw)
        return field, op, value

    def _maybe_emit(self, ctx: Any, rec: Mapping[str, Any]) -> bool:
        field, op, value = self._threshold()
        raw: Any = rec.get(field) if field else rec.get("value")
        if raw is None:
            return False
        try:
            num = float(raw)
        except (TypeError, ValueError):
            return False
        if not _crosses(num, op, value):
            return False
        emit = self._emit_name()
        emit_event = getattr(ctx, "emit_event", None)
        if callable(emit_event):
            emit_event(emit, {"status": 1})
            return True
        key = getattr(ctx, "run_key", None)
        if key is not None:
            get_environment_hub().emit(key, emit, {"status": 1})
            return True
        return False

    def on_batch(self, batch: list, ctx: Any) -> None:
        for rec in batch or ():
            if isinstance(rec, Mapping):
                self._maybe_emit(ctx, rec)

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        del ctx
        return ActionResult(payload={})

    async def execute(self, ctx: Any) -> StepOutput:
        fired = False
        try:
            data = ctx.data
        except AttributeError:
            data = {}
        if isinstance(data, Mapping):
            fired = self._maybe_emit(ctx, data)
        try:
            records = ctx.records
        except AttributeError:
            records = ()
        if not fired and isinstance(records, list):
            for rec in records:
                if isinstance(rec, Mapping) and self._maybe_emit(ctx, rec):
                    fired = True
                    break
        return StepOutput(metadata={"fired": fired})
