#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Budget governance — token/tool-call ceilings on egress hot path."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetTracker:
    max_tool_calls: int | None = None
    max_llm_calls: int | None = None
    tool_calls: int = 0
    llm_calls: int = 0

    def allow_llm(self) -> bool:
        if self.max_llm_calls is None:
            return True
        return self.llm_calls < self.max_llm_calls

    def allow_tool(self) -> bool:
        if self.max_tool_calls is None:
            return True
        return self.tool_calls < self.max_tool_calls

    def note_llm(self) -> None:
        self.llm_calls += 1

    def note_tool(self) -> None:
        self.tool_calls += 1


def budget_from_manifest(manifest: dict | None) -> BudgetTracker:
    """Build a budget tracker from explicit manifest config only.

    Ceilings are not applied by default. When no ``spec.budget`` is set,
    tool and LLM calls are unlimited.
    """
    spec = (manifest or {}).get("spec") or {}
    budget = spec.get("budget") or {}
    if not isinstance(budget, dict):
        budget = {}
    return BudgetTracker(
        max_tool_calls=budget.get("max_tool_calls"),
        max_llm_calls=budget.get("max_llm_calls"),
    )
