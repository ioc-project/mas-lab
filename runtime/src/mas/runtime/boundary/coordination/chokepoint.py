#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Chokepoint coordination — M_coord forces gov/obs at engine boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mas.runtime.kernel.state import DpState, QProduct
from mas.runtime.machines.gov import GovState
from mas.runtime.schema.observability import ObsPhase


class ObsState(str, Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    FLUSHING = "FLUSHING"


class ChokepointKind(str, Enum):
    INTERNAL = "INTERNAL"
    EGRESS_TO_ENGINE = "EGRESS_TO_ENGINE"
    INGRESS_FROM_ENGINE = "INGRESS_FROM_ENGINE"


@dataclass
class ChokepointCoordinator:
    """Mirrors TLA M_coord + gov/obs local machines at product boundary."""

    gov_state: GovState = GovState.IDLE
    obs_state: ObsState = ObsState.IDLE
    coord_state: str = "IDLE"
    mutation_count: int = 0

    def before_egress_governance(self, q: QProduct) -> None:
        self.gov_state = GovState.AUTHZ_EGRESS
        self.coord_state = "FAN_OUT"
        self._record_mutation(q, ChokepointKind.EGRESS_TO_ENGINE, ObsPhase.AUTHZ)

    def after_egress_allowed(self, q: QProduct) -> None:
        self.gov_state = GovState.VALIDATING
        self._record_mutation(q, ChokepointKind.EGRESS_TO_ENGINE, ObsPhase.EXECUTE)

    def on_egress_hitl(self, q: QProduct) -> None:
        self.gov_state = GovState.HITL_PENDING
        self._record_mutation(q, ChokepointKind.EGRESS_TO_ENGINE, ObsPhase.AUTHZ)

    def on_egress_blocked(self, q: QProduct) -> None:
        self.gov_state = GovState.BLOCKED
        self._record_mutation(q, ChokepointKind.EGRESS_TO_ENGINE, ObsPhase.AUTHZ)

    def on_gov_error(self, q: QProduct) -> None:
        self.gov_state = GovState.ERROR
        self._record_mutation(q, ChokepointKind.INTERNAL, ObsPhase.AUTHZ)

    def before_ingress_governance(self, q: QProduct) -> None:
        self.gov_state = GovState.AUTHZ_INGRESS
        self.coord_state = "BARRIER"
        self._record_mutation(q, ChokepointKind.INGRESS_FROM_ENGINE, ObsPhase.RESULT)

    def after_ingress_allowed(self, q: QProduct) -> None:
        self.gov_state = GovState.IDLE
        self._record_mutation(q, ChokepointKind.INGRESS_FROM_ENGINE, ObsPhase.VALID)

    def on_internal_mutation(self, q: QProduct, *, label: str = "internal") -> None:
        self.coord_state = "IDLE"
        self._record_mutation(q, ChokepointKind.INTERNAL, ObsPhase.REQUEST, label=label)

    def audit(self, q: QProduct, *, tau_len: int) -> list[str]:
        gaps: list[str] = []
        if q.scheduled_egress != "NONE" and q.dp == DpState.EGRESS_PENDING:
            if self.gov_state not in {
                GovState.AUTHZ_EGRESS,
                GovState.VALIDATING,
                GovState.HITL_PENDING,
                GovState.BLOCKED,
                GovState.ERROR,
            }:
                gaps.append("egress scheduled without active governance chokepoint")
        if tau_len > self.mutation_count:
            gaps.append("tau mutations exceed recorded obs mutations")
        if self.mutation_count > 0 and self.obs_state == ObsState.IDLE:
            gaps.append("obs idle after mutations started")
        return gaps

    def _record_mutation(
        self,
        q: QProduct,
        kind: ChokepointKind,
        phase: ObsPhase,
        *,
        label: str = "",
    ) -> None:
        self.obs_state = ObsState.RECORDING
        self.mutation_count += 1
