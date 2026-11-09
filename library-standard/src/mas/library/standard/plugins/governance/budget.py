#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Call ceilings as a governance plugin: count LLM and tool calls at egress, stop the run at the cap."""

from __future__ import annotations

from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "budget"


def _ceiling(name: str, raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"budget.{name} must be an integer >= 0, got {raw!r}")
    return raw


class BudgetPlugin:
    """TERMINATE once a run has issued ``max_llm_calls`` LLM calls or ``max_tool_calls`` tool calls.

    A field left unset has no limit. Every egress of those two operations is counted once; a call
    that was already counted (the same operation and correlation id, evaluated again after a human
    approval) passes. It passes every other operation.
    """

    plugin_id = "budget@v1"

    def __init__(self, max_llm_calls: int | None = None, max_tool_calls: int | None = None) -> None:
        self.max_llm_calls = _ceiling("max_llm_calls", max_llm_calls)
        self.max_tool_calls = _ceiling("max_tool_calls", max_tool_calls)
        self.llm_calls = 0
        self.tool_calls = 0
        self._counted: set[tuple[str, int]] = set()

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        if intent.op == "LLM_CALL":
            cap, used, label = self.max_llm_calls, self.llm_calls, "LLM calls"
        elif intent.op == "TOOL_CALL":
            cap, used, label = self.max_tool_calls, self.tool_calls, "tool calls"
        else:
            return GovDecision.ALLOW, PLUGIN_ID, "not an LLM or tool call"
        key = (intent.op, intent.correlation_id)
        if key in self._counted:
            return GovDecision.ALLOW, PLUGIN_ID, "this call was already counted"
        if cap is not None and used >= cap:
            return GovDecision.TERMINATE, PLUGIN_ID, f"budget exceeded: {used} of {cap} {label} used"
        self._counted.add(key)
        if intent.op == "LLM_CALL":
            self.llm_calls += 1
        else:
            self.tool_calls += 1
        return GovDecision.ALLOW, PLUGIN_ID, f"{label} within budget"
