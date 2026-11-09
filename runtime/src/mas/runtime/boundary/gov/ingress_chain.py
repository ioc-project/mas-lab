#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""First-match ingress governance plugin chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from mas.runtime.boundary.gov.filter import GovTransitionFilter
from mas.runtime.boundary.gov.ingress_plugin import (
    IngressGovDecision,
    IngressIntentView,
    ingress_from_profile,
)
from mas.runtime.kernel.config import KernelConfig


class ChainableIngressPlugin(Protocol):
    plugin_id: str

    def evaluate_ingress(self, intent: IngressIntentView, *, config: KernelConfig) -> IngressGovDecision: ...


@dataclass(frozen=True)
class RegisteredIngressPlugin:
    plugin: ChainableIngressPlugin
    filter: GovTransitionFilter = field(default_factory=GovTransitionFilter)
    chain: Literal["stop", "continue"] = "stop"
    priority: int = 0


def evaluate_ingress_chain(
    intent: IngressIntentView,
    *,
    config: KernelConfig,
    plugins: list[RegisteredIngressPlugin] | None = None,
) -> IngressGovDecision:
    """First matching plugin wins unless it returns chain=continue."""
    chain = plugins if plugins is not None else list(getattr(config, "ingress_governance_plugins", ()))
    for entry in chain:
        if not entry.filter.matches_ingress(intent):
            continue
        decision = entry.plugin.evaluate_ingress(intent, config=config)
        if decision.chain == "continue" or entry.chain == "continue":
            continue
        _log_ingress(intent, decision)
        return decision
    decision = ingress_from_profile(intent)
    _log_ingress(intent, decision)
    return decision


def _log_ingress(intent: IngressIntentView, decision: IngressGovDecision) -> None:
    from mas.runtime.reliability.log import log_ingress

    log_ingress(
        action=decision.action.value if hasattr(decision.action, "value") else str(decision.action),
        failure_class=intent.failure_class,
        failure_code=intent.failure_code,
        retry_count=intent.retry_count,
        max_retries=intent.max_retries,
        message=decision.message,
    )
