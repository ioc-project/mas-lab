#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for Mealy envelope composition on tool crossings."""

from __future__ import annotations

from dataclasses import replace

from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.envelope import (
    EnvelopeContext,
    GuardedProductComposer,
    close_envelope,
    contract_kind_for_op,
    execute_contract_call,
    run_egress_authorize_envelope,
    run_ingress_validate_envelope,
)
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.envelope import (
    EGRESS_ENVELOPE_SYMBOLS,
    INGRESS_ENVELOPE_SYMBOLS,
    EnvelopeSymbol,
    resolve_egress_symbols,
    resolve_ingress_symbols,
)
from mas.runtime.schema.ingress import EngineIoReturn
from mas.runtime.schema.observability import ObsEventKind


def _activity_symbols(ctx: EnvelopeContext) -> list[str]:
    return [
        e.payload.get("symbol")
        for e in ctx.observability.events  # type: ignore[union-attr]
        if e.kind == ObsEventKind.ENVELOPE_ACTIVITY
    ]


def _ctx(*, op: str = "TOOL_CALL", cid: int = 1) -> EnvelopeContext:
    return EnvelopeContext(
        q=QProduct(),
        correlation_id=cid,
        contract=contract_kind_for_op(op),
        scheduled_op=op,
        observability=ObservabilityOperator(),
        config=KernelConfig(hitl_on_tool=False),
        tool_name="web-search",
        tool_arguments={"q": "test"},
    )


def test_egress_envelope_emits_all_symbols() -> None:
    ctx = _ctx()
    run_egress_authorize_envelope(ctx)
    symbols = [e.payload.get("symbol") for e in ctx.observability.events if e.kind == ObsEventKind.ENVELOPE_ACTIVITY]  # type: ignore[union-attr]
    assert symbols == [s.value for s in EGRESS_ENVELOPE_SYMBOLS]


def test_ingress_envelope_emits_all_symbols() -> None:
    ctx = _ctx()
    ctx.ingress_event = EngineIoReturn(
        correlation_id=1,
        response_kind="TOOL_RESULT",
        next_step="STOP",
        text="result",
    )
    run_ingress_validate_envelope(ctx)
    symbols = [e.payload.get("symbol") for e in ctx.observability.events if e.kind == ObsEventKind.ENVELOPE_ACTIVITY]  # type: ignore[union-attr]
    assert symbols == [s.value for s in INGRESS_ENVELOPE_SYMBOLS]


def test_governance_decisions_before_and_after() -> None:
    ctx = _ctx()
    run_egress_authorize_envelope(ctx)
    ctx.ingress_event = EngineIoReturn(
        correlation_id=1,
        response_kind="TOOL_RESULT",
        next_step="STOP",
        text="ok",
    )
    run_ingress_validate_envelope(ctx)
    gov = [e.payload for e in ctx.observability.events if e.kind == ObsEventKind.GOVERNANCE_DECISION]  # type: ignore[union-attr]
    assert len(gov) >= 4
    egress = [p for p in gov if p.get("hook") == "egress"]
    ingress = [p for p in gov if p.get("hook") == "ingress"]
    assert any(p.get("checkpoint") == "before" for p in egress)
    assert any(p.get("checkpoint") == "after" for p in egress)
    assert any(p.get("checkpoint") == "before" for p in ingress)
    assert any(p.get("checkpoint") == "after" for p in ingress)


def test_execute_contract_call_in_process() -> None:
    ctx = _ctx()
    seen: list[str] = []

    def _run() -> str:
        seen.append("execute")
        return "ok"

    result = execute_contract_call(ctx.contract, "call", ctx, execute_fn=_run)
    assert result == "ok"
    assert seen == ["execute"]
    assert any(e.kind == ObsEventKind.ENGINE_IO for e in ctx.observability.events)  # type: ignore[union-attr]


def test_execute_contract_call_closes_on_error() -> None:
    """CONTRACT_EXECUTE is always followed by the ingress close, even when
    execute_fn raises — the failure is recorded as ENGINE_IO_RETURN.
    """
    ctx = _ctx()

    def _boom() -> str:
        raise RuntimeError("Tool 'get_deployments' not found in manifest or overlays")

    try:
        execute_contract_call(ctx.contract, "call", ctx, execute_fn=_boom)
        raise AssertionError("expected execute_fn to raise")
    except RuntimeError as exc:
        assert "get_deployments" in str(exc)

    kinds = [e.kind for e in ctx.observability.events]  # type: ignore[union-attr]
    assert ObsEventKind.ENGINE_IO in kinds
    assert ObsEventKind.ENGINE_IO_RETURN in kinds
    ends = [e for e in ctx.observability.events if e.kind == ObsEventKind.ENGINE_IO_RETURN]  # type: ignore[union-attr]
    assert ends[-1].payload.get("response_kind") == "ERROR"
    assert "get_deployments" in str(ends[-1].payload.get("text") or "")
    assert ends[-1].payload.get("op") == "TOOL_CALL"
    symbols = _activity_symbols(ctx)
    assert EnvelopeSymbol.CONTRACT_EXECUTE.value in symbols
    assert symbols[-len(INGRESS_ENVELOPE_SYMBOLS) :] == [s.value for s in INGRESS_ENVELOPE_SYMBOLS]


def test_close_envelope_emits_all_ingress_symbols_on_error() -> None:
    ctx = _ctx()
    run_egress_authorize_envelope(ctx)
    close_envelope(ctx, error="GOV_BLOCK")
    expected = [s.value for s in EGRESS_ENVELOPE_SYMBOLS] + [s.value for s in INGRESS_ENVELOPE_SYMBOLS]
    assert _activity_symbols(ctx) == expected
    ends = [e for e in ctx.observability.events if e.kind == ObsEventKind.ENGINE_IO_RETURN]  # type: ignore[union-attr]
    assert ends[-1].payload.get("response_kind") == "ERROR"
    assert "GOV_BLOCK" in str(ends[-1].payload.get("text") or "")


def test_composer_continues_after_machine_failure() -> None:
    class _Boom:
        machine_id = "boom"

        def step(self, symbol: EnvelopeSymbol, ctx: EnvelopeContext) -> None:
            raise RuntimeError("machine boom")

    class _Ok:
        machine_id = "ok"
        seen: list[EnvelopeSymbol]

        def __init__(self) -> None:
            self.seen = []

        def step(self, symbol: EnvelopeSymbol, ctx: EnvelopeContext) -> None:
            self.seen.append(symbol)

    ok = _Ok()
    composer = GuardedProductComposer(machines=[_Boom(), ok])  # type: ignore[arg-type]
    ctx = _ctx()
    composer.step(EnvelopeSymbol.CONTRACT_START, ctx)
    composer.step(EnvelopeSymbol.CONTRACT_END, ctx)
    assert ok.seen == [EnvelopeSymbol.CONTRACT_START, EnvelopeSymbol.CONTRACT_END]


def test_egress_block_closes_contract_envelope() -> None:
    from mas.runtime.kernel.coupling import GovDecision
    from mas.runtime.kernel.egress_gate import emit_scheduled_egress
    from mas.runtime.kernel.inflight import pending_for_validate
    from mas.runtime.kernel.runtime_context import runtime_binding
    from mas.runtime.kernel.state import DpState, RunLedger
    from mas.runtime.schema.egress import NoOp

    class _BlockPlugin:
        def evaluate_egress(self, intent, *, config):  # noqa: ANN001
            return GovDecision.BLOCK, "test-block", "blocked for test"

    ctx_obs = ObservabilityOperator()
    q = QProduct()
    q.scheduled_egress = "TOOL_CALL"
    q.pending_tool_name = "web-search"
    run = RunLedger()
    config = KernelConfig(hitl_on_tool=False, egress_governance_plugin=_BlockPlugin())
    with runtime_binding(None, ctx_obs):
        out = emit_scheduled_egress(q, run, config)
    assert isinstance(out[0], NoOp)
    assert pending_for_validate(q) == []
    assert q.dp == DpState.EVALUATING
    assert run.events[-1].placeholder
    assert "blocked for test" in (run.events[-1].text or "")
    symbols = [e.payload.get("symbol") for e in ctx_obs.events if e.kind == ObsEventKind.ENVELOPE_ACTIVITY]
    assert EnvelopeSymbol.CONTRACT_START.value in symbols
    assert EnvelopeSymbol.CONTRACT_END.value in symbols


def test_envelope_works_without_governance_plugins() -> None:
    ctx = _ctx()
    ctx.config = KernelConfig(hitl_on_tool=False, gov_block_destructive=False)
    decision = run_egress_authorize_envelope(ctx)
    assert decision.value == "ALLOW"
    assert ctx.observability is not None
    assert any(e.kind == ObsEventKind.ENVELOPE_ACTIVITY for e in ctx.observability.events)


def test_contract_kind_memory_and_transport() -> None:
    assert contract_kind_for_op("MEMORY_OP").value == "memory"
    assert contract_kind_for_op("TRANSPORT_MSG").value == "transport"
    assert contract_kind_for_op("LLM_CALL").value == "model"


def test_egress_without_gov_collapses_symbols_and_skips_policy() -> None:
    ctx = _ctx()
    ctx.config = replace(
        KernelConfig(hitl_on_tool=True, gov_block_destructive=True),
        enable_governance=False,
    )
    run_egress_authorize_envelope(ctx)
    expected = resolve_egress_symbols(
        enable_governance=False,
        enable_envelope_observability=True,
    )
    symbols = [
        e.payload.get("symbol")
        for e in ctx.observability.events  # type: ignore[union-attr]
        if e.kind == ObsEventKind.ENVELOPE_ACTIVITY
    ]
    assert symbols == [s.value for s in expected]
    assert not any(
        e.kind == ObsEventKind.GOVERNANCE_DECISION
        for e in ctx.observability.events  # type: ignore[union-attr]
    )


def test_egress_without_obs_collapses_symbols() -> None:
    ctx = _ctx()
    ctx.config = replace(KernelConfig(hitl_on_tool=False), enable_envelope_observability=False)
    run_egress_authorize_envelope(ctx)
    expected = resolve_egress_symbols(
        enable_governance=True,
        enable_envelope_observability=False,
    )
    assert expected == (resolve_egress_symbols(enable_governance=True, enable_envelope_observability=False))
    symbols = [
        e.payload.get("symbol")
        for e in ctx.observability.events  # type: ignore[union-attr]
        if e.kind == ObsEventKind.ENVELOPE_ACTIVITY
    ]
    assert symbols == []
    gov = [
        e.payload
        for e in ctx.observability.events  # type: ignore[union-attr]
        if e.kind == ObsEventKind.GOVERNANCE_DECISION
    ]
    assert len(gov) >= 2


def test_minimal_envelope_without_obs_or_gov() -> None:
    ctx = _ctx()
    ctx.config = replace(
        KernelConfig(hitl_on_tool=True),
        enable_governance=False,
        enable_envelope_observability=False,
    )
    run_egress_authorize_envelope(ctx)
    egress = resolve_egress_symbols(enable_governance=False, enable_envelope_observability=False)
    assert egress == (EnvelopeSymbol.CONTRACT_START,)
    symbols = [
        e.payload.get("symbol")
        for e in ctx.observability.events  # type: ignore[union-attr]
        if e.kind == ObsEventKind.ENVELOPE_ACTIVITY
    ]
    assert symbols == []
    ctx.ingress_event = EngineIoReturn(
        correlation_id=1,
        response_kind="TOOL_RESULT",
        next_step="STOP",
        text="ok",
    )
    run_ingress_validate_envelope(ctx)
    ingress = resolve_ingress_symbols(enable_governance=False, enable_envelope_observability=False)
    assert ingress == (EnvelopeSymbol.CONTRACT_END,)
