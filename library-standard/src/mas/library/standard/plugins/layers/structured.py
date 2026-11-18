#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Ask a model for schema-conforming JSON and return a validated ``parsed`` object."""

from __future__ import annotations

from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.library.standard.plugins.llm.structured import apply_structured_result, json_schema_format, schema_from_format
from mas.runtime.engine.ainvoke import ainvoke_call
from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class StructuredLayer(LayerLifecycle):
    """Opt-in wrap: inject ``response_format`` and validate the completion through ``_provider_result_return``."""

    per = "destination"
    call_kinds = ("llm",)
    priority = 40

    def __init__(self, inner: Any = None, *, schema: dict[str, Any] | None = None, name: str = "result") -> None:
        super().__init__()
        self.inner = inner
        self.schema = dict(schema) if isinstance(schema, dict) else None
        self.name = str(name or "result")

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "StructuredLayer":
        raw = dict(params or {})
        schema = (
            raw.get("schema") if isinstance(raw.get("schema"), dict) else schema_from_format(raw.get("response_format"))
        )
        wrapped = cls(inner=inner, schema=schema, name=str(raw.get("name") or "result"))
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not call_kind_matches(io.op, self.call_kinds):
            return await ainvoke_call(self.inner, io)
        payload = _inject(self.schema, self.name)
        return _validate(io, payload, self.schema, await ainvoke_call(self.inner, io))


def _inject(schema: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL

    payload = _PROVIDER_CALL.get()
    if not isinstance(payload, dict):
        return None
    fmt = payload.get("response_format")
    resolved = schema or schema_from_format(fmt)
    if not resolved:
        return payload
    payload["response_format"] = json_schema_format(resolved, name=name)
    return payload


def _validate(
    io: InvokeEngineIo, payload: dict[str, Any] | None, schema: dict[str, Any] | None, ret: EngineIoReturn
) -> EngineIoReturn:
    from mas.runtime.engine.infra_pipeline import _provider_result_return

    if not isinstance(payload, dict):
        return ret
    resolved = schema or schema_from_format(payload.get("response_format"))
    result = payload.get("_result")
    if not resolved or not isinstance(result, dict):
        return ret
    payload["_result"] = apply_structured_result(result, resolved)
    return _provider_result_return(io, payload, payload["_result"])
