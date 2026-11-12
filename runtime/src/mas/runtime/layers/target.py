#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a call layer needs to know about the call in front of it: target, failure class, repeatability."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mas.runtime.engine.call_kinds import op_kind
from mas.runtime.engine.infra_pipeline import call_api_base, call_ctx, call_model
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, tool_is_idempotent
from mas.runtime.layers.state import running_spec_of
from mas.runtime.reliability.classes import ClassifiedFailure
from mas.runtime.reliability.classify import classify_llm_failure, classify_tool_exception
from mas.runtime.schema.egress import InvokeEngineIo


def tool_in_flight_name() -> str:
    """The tool the engine of this context is dispatching, or ``""`` when no engine announced one."""
    current = IN_FLIGHT_TOOL.get()
    return current.name if current is not None else ""


def call_target(inner: Any, io: InvokeEngineIo) -> str | None:
    """``tool:<name>`` or ``llm:<api base>``. ``None`` for a tool call whose tool is not known.

    A layer keeps state per target; with no name there is no target, so such a call is passed through
    rather than pooled with every other nameless one.
    """
    if op_kind(io.op) == "tool":
        name = tool_in_flight_name()
        return f"tool:{name}" if name else None
    return f"llm:{call_api_base(inner) or call_model(inner) or 'llm'}"


def call_is_idempotent(inner: Any, io: InvokeEngineIo) -> bool:
    """LLM calls repeat freely. A tool repeats only when the running spec declares it ``idempotent``."""
    if op_kind(io.op) != "tool":
        return True
    name = tool_in_flight_name()
    return bool(name) and tool_is_idempotent(name, running_spec_of(call_ctx(inner)))


def classifier_for(io: InvokeEngineIo) -> Callable[[BaseException], ClassifiedFailure]:
    return classify_tool_exception if op_kind(io.op) == "tool" else classify_llm_failure
