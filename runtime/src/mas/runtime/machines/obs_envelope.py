#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""M_obs envelope δ — steps on every σ; emits activity start/end for spans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mas.runtime.schema.envelope import EnvelopeSymbol
from mas.runtime.schema.observability import ObsPhase

if TYPE_CHECKING:
    from mas.runtime.kernel.envelope import EnvelopeContext


_SYMBOL_ACTIVITY: dict[EnvelopeSymbol, str] = {
    EnvelopeSymbol.OBS_WRAP_GOV_AUTHORIZE_START: "obs_wrap_gov_authorize",
    EnvelopeSymbol.OBS_WRAP_GOV_AUTHORIZE_END: "obs_wrap_gov_authorize",
    EnvelopeSymbol.GOV_AUTHORIZE_START: "gov_authorize",
    EnvelopeSymbol.GOV_AUTHORIZE_END: "gov_authorize",
    EnvelopeSymbol.GOVERNANCE_AUTHORIZE: "governance_authorize",
    EnvelopeSymbol.OBS_WRAP_GOV_VALIDATE_START: "obs_wrap_gov_validate",
    EnvelopeSymbol.OBS_WRAP_GOV_VALIDATE_END: "obs_wrap_gov_validate",
    EnvelopeSymbol.GOV_VALIDATE_START: "gov_validate",
    EnvelopeSymbol.GOV_VALIDATE_END: "gov_validate",
    EnvelopeSymbol.GOVERNANCE_VALIDATE: "governance_validate",
    EnvelopeSymbol.CONTRACT_START: "contract_call",
    EnvelopeSymbol.CONTRACT_END: "contract_call",
    EnvelopeSymbol.OBSERVABILITY_PRE_EXECUTE: "observability_pre_execute",
    EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE: "observability_post_execute",
    EnvelopeSymbol.CONTRACT_EXECUTE: "contract_execute",
}

_SYMBOL_BOUNDARY: dict[EnvelopeSymbol, str] = {
    EnvelopeSymbol.OBS_WRAP_GOV_AUTHORIZE_START: "start",
    EnvelopeSymbol.OBS_WRAP_GOV_AUTHORIZE_END: "end",
    EnvelopeSymbol.GOV_AUTHORIZE_START: "start",
    EnvelopeSymbol.GOV_AUTHORIZE_END: "end",
    EnvelopeSymbol.GOV_VALIDATE_START: "start",
    EnvelopeSymbol.GOV_VALIDATE_END: "end",
    EnvelopeSymbol.OBS_WRAP_GOV_VALIDATE_START: "start",
    EnvelopeSymbol.OBS_WRAP_GOV_VALIDATE_END: "end",
    EnvelopeSymbol.CONTRACT_START: "start",
    EnvelopeSymbol.CONTRACT_END: "end",
    EnvelopeSymbol.OBSERVABILITY_PRE_EXECUTE: "start",
    EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE: "end",
    EnvelopeSymbol.CONTRACT_EXECUTE: "event",
}

_SYMBOL_PHASE: dict[EnvelopeSymbol, ObsPhase] = {
    EnvelopeSymbol.GOVERNANCE_AUTHORIZE: ObsPhase.AUTHZ,
    EnvelopeSymbol.GOVERNANCE_VALIDATE: ObsPhase.VALID,
    EnvelopeSymbol.OBSERVABILITY_PRE_EXECUTE: ObsPhase.OBSERVABILITY_PRE,
    EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE: ObsPhase.RESULT,
    EnvelopeSymbol.CONTRACT_EXECUTE: ObsPhase.EXECUTE,
}


@dataclass
class ObsEnvelopeMachine:
    """M_obs_j — records every envelope σ (span bookends via start/end events)."""

    machine_id: str = "M_obs"

    def step(self, symbol: EnvelopeSymbol, ctx: EnvelopeContext) -> None:
        obs = ctx.observability
        if obs is None:
            return
        activity = _SYMBOL_ACTIVITY.get(symbol, symbol.value)
        boundary = _SYMBOL_BOUNDARY.get(symbol, "event")
        phase = _SYMBOL_PHASE.get(symbol, ObsPhase.REQUEST)
        payload: dict[str, Any] = {
            "symbol": symbol.value,
            "activity": activity,
            "boundary": boundary,
            "contract": ctx.contract.value,
            "operation": ctx.operation,
            "op": ctx.scheduled_op,
        }
        if symbol == EnvelopeSymbol.GOVERNANCE_AUTHORIZE:
            payload["decision"] = ctx.gov_decision
            payload["hook"] = "egress"
            payload["checkpoint"] = "authorize"
        elif symbol == EnvelopeSymbol.GOVERNANCE_VALIDATE:
            payload["decision"] = ctx.gov_decision
            payload["hook"] = "ingress"
            payload["checkpoint"] = "validate"
        elif symbol in (
            EnvelopeSymbol.OBSERVABILITY_PRE_EXECUTE,
            EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE,
        ):
            payload["tool_name"] = str(ctx.tool_name or ctx.scheduled_op or activity)
            if ctx.ingress_event is not None:
                ev = ctx.ingress_event
                payload["response_kind"] = ev.response_kind
                if symbol == EnvelopeSymbol.OBSERVABILITY_POST_EXECUTE:
                    # This branch used to be shadowed: the previous code had
                    # OBSERVABILITY_POST_EXECUTE matched by BOTH this elif and
                    # a separate `elif symbol == OBSERVABILITY_POST_EXECUTE`
                    # further down in the same chain — the first match always
                    # wins in an if/elif chain, so the second branch (the one
                    # that actually recorded the call's end) never ran, for
                    # ANY op. tool_call_end/memory_call_end were never emitted
                    # at all; only LLM_CALL got an end event, via a separate
                    # hand-written special case in driver.py (now removed —
                    # see driver.py's _record_engine_return).
                    ret_record = getattr(obs, "record_engine_io_return", None)
                    if callable(ret_record):
                        ret_kwargs: dict[str, Any] = {
                            "correlation_id": ctx.correlation_id,
                            "op": ctx.scheduled_op,
                            "text": ev.text,
                            "next_step": ev.next_step,
                            "response_kind": ev.response_kind,
                            # Without this, the native layer's resolve_tool_name
                            # falls back to the literal string "tool" (no
                            # tool_name in this payload at all), which never
                            # matches tool_call_start's real tool name in
                            # records.py's (agent_id, kind_base, tool_name)
                            # pairing key — the end event was silently dropped
                            # and the record stayed _end_missing despite this
                            # branch actually firing. Same field CONTRACT_EXECUTE
                            # already passes to record_engine_io below for the
                            # matching start event.
                            "tool_name": ctx.tool_name,
                            "usage": ev.usage,
                            "finish_reason": ev.finish_reason,
                            "cache_status": getattr(ev, "cache_status", None),
                            "cache_layer": getattr(ev, "cache_layer", "") or "",
                            "cache_events": list(getattr(ev, "cache_events", []) or []),
                            "pricing": dict(getattr(ev, "pricing", {}) or {}),
                        }
                        if getattr(ev, "truncation_retries", 0):
                            ret_kwargs["truncation_retries"] = ev.truncation_retries
                        if getattr(ev, "retry_attempts", 0):
                            ret_kwargs["retry_attempts"] = ev.retry_attempts
                        if getattr(ev, "failure_class", ""):
                            ret_kwargs["failure_class"] = ev.failure_class
                        if getattr(ev, "failure_code", ""):
                            ret_kwargs["failure_code"] = ev.failure_code
                        if ctx.scheduled_op == "LLM_CALL":
                            tool_calls = _llm_return_tool_calls(ev)
                            if tool_calls:
                                ret_kwargs["tool_calls"] = tool_calls
                            # The operator drops it when it equals the cap asked for on start.
                            if getattr(ev, "max_tokens", None) is not None:
                                ret_kwargs["max_tokens"] = ev.max_tokens
                        ret_record(**ret_kwargs)
        elif symbol == EnvelopeSymbol.CONTRACT_EXECUTE:
            payload["tool_name"] = str(ctx.tool_name or ctx.scheduled_op or ctx.operation or "tool")
            payload["tool_arguments"] = dict(ctx.tool_arguments or {})
            io_record = getattr(obs, "record_engine_io", None)
            if callable(io_record):
                request = ctx.llm_request if ctx.scheduled_op == "LLM_CALL" else None
                extras = request.engine_io_fields() if request is not None else {}
                io_record(
                    correlation_id=ctx.correlation_id,
                    op=ctx.scheduled_op,
                    destructive=ctx.destructive,
                    tool_name=ctx.tool_name,
                    tool_arguments=dict(ctx.tool_arguments or {}),
                    **extras,
                )
        record = getattr(obs, "record_envelope_activity", None)
        if callable(record):
            record(
                symbol=symbol.value,
                activity=activity,
                boundary=boundary,
                phase=phase,
                correlation_id=ctx.correlation_id,
                machine_id=self.machine_id,
                payload=payload,
            )


def _llm_return_tool_calls(ev: Any) -> list[dict[str, Any]] | None:
    """Structured tool calls the model asked for. Request-side tools stay on start."""
    if getattr(ev, "next_step", "") == "PARALLEL_TOOL_CALLS":
        specs = list(getattr(ev, "parallel_tools", ()) or ())
        return [
            {
                "toolName": str(getattr(spec, "tool_name", "") or ""),
                "arguments": dict(getattr(spec, "tool_arguments", None) or {}),
            }
            for spec in specs
        ] or None
    name = str(getattr(ev, "tool_name", "") or "")
    if name:
        return [
            {
                "toolName": name,
                "arguments": dict(getattr(ev, "tool_arguments", None) or {}),
            }
        ]
    return None
