#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Await one engine call or exchange preview, whatever shape the engine has."""

from __future__ import annotations

from typing import Any

from mas.runtime.aio import run_sync, warn_sync_plugin
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


async def ainvoke_call(engine: Any, io: InvokeEngineIo) -> EngineIoReturn:
    """Await ``engine.ainvoke``. An engine that only has a blocking ``invoke`` runs it in a worker thread."""
    ainvoke = getattr(engine, "ainvoke", None)
    if callable(ainvoke):
        return await ainvoke(io)
    warn_sync_plugin(engine, "invoke", "ainvoke")
    return await run_sync(engine.invoke, io)


async def apreview_call(engine: Any, op: str, *, correlation_id: int = 0) -> str:
    """The preview text of one call: ``aexchange_preview`` when the engine has it, else its sync twin."""
    apreview = getattr(engine, "aexchange_preview", None)
    if callable(apreview):
        return str(await apreview(op, correlation_id=correlation_id) or "")
    preview = getattr(engine, "exchange_preview", None)
    return str(preview(op, correlation_id=correlation_id) or "") if callable(preview) else ""


__all__ = ["ainvoke_call", "apreview_call"]
