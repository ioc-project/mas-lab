#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""M_gov envelope δ — governance telemetry and state on authorize/validate symbols.

Policy evaluation runs in ``kernel.envelope`` (``_evaluate_egress`` /
``_evaluate_ingress``) *before* ``composer.step(GOVERNANCE_*)``. This machine
records ``gov_state`` transitions and emits ``governance.decision`` events;
``pass`` on authorize/validate symbols is intentional — decisions are on
``EnvelopeContext.gov_decision`` when ``GOV_*_END`` runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from mas.runtime.kernel.envelope import EnvelopeContext
from mas.runtime.machines.gov import GovState, gov_on_egress_allowed, gov_on_idle
from mas.runtime.schema.envelope import EnvelopeSymbol


def _holds_a_question(q) -> bool:
    """A human answer is pending: closing another call's envelope must not clear it."""
    return bool(q.hitl_holds)


@dataclass
class GovEnvelopeMachine:
    """M_gov — local state transitions + governance.decision on authorize/validate σ."""

    machine_id: str = "M_gov"

    def step(self, symbol: EnvelopeSymbol, ctx: EnvelopeContext) -> None:
        q = ctx.q
        if symbol == EnvelopeSymbol.GOV_AUTHORIZE_START:
            if q.gov_state == GovState.ERROR.value:
                return
            q.gov_state = GovState.AUTHZ_EGRESS.value
            self._record(ctx, hook="egress", checkpoint="before")
        elif symbol == EnvelopeSymbol.GOVERNANCE_AUTHORIZE:
            pass
        elif symbol == EnvelopeSymbol.GOV_AUTHORIZE_END:
            self._record(ctx, hook="egress", checkpoint="after")
            if q.gov_state == GovState.ERROR.value:
                return
            if ctx.gov_decision not in ("BLOCK", "TERMINATE", "HITL"):
                gov_on_egress_allowed(q)
        elif symbol == EnvelopeSymbol.GOV_VALIDATE_START:
            if _holds_a_question(q):
                self._record(ctx, hook="ingress", checkpoint="before")
                return
            q.gov_state = GovState.AUTHZ_INGRESS.value
            self._record(ctx, hook="ingress", checkpoint="before")
        elif symbol == EnvelopeSymbol.GOVERNANCE_VALIDATE:
            pass
        elif symbol == EnvelopeSymbol.GOV_VALIDATE_END:
            self._record(ctx, hook="ingress", checkpoint="after")
            if q.gov_state != GovState.ERROR.value and not _holds_a_question(q):
                gov_on_idle(q)

    def _record(self, ctx: EnvelopeContext, *, hook: str, checkpoint: str) -> None:
        obs = ctx.observability
        if obs is None:
            return
        from mas.runtime.schema.observability import ObsPhase

        obs_phase = ObsPhase.AUTHZ if hook == "egress" else ObsPhase.VALID
        if checkpoint == "after" and hook == "ingress":
            obs_phase = ObsPhase.VALID
        decision = ctx.gov_decision if checkpoint == "after" else ""
        reason = ctx.gov_reason if checkpoint == "after" else ""
        ev = ctx.ingress_event
        obs.record_governance_decision(
            hook=hook,
            phase=checkpoint,
            decision=decision,
            reason=reason,
            correlation_id=ctx.correlation_id,
            policy_name=ctx.policy_name or "kernel",
            obs_phase=obs_phase,
            op=ctx.scheduled_op,
            failure_class=getattr(ev, "failure_class", "") or "" if hook == "ingress" else "",
            failure_code=getattr(ev, "failure_code", "") or "" if hook == "ingress" else "",
            retry_attempts=getattr(ev, "retry_attempts", 0) or 0 if hook == "ingress" else 0,
        )
