#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Human-readable HITL choice labels for operators."""

from __future__ import annotations

from mas.runtime.schema.hitl import HitlResolveChoice

HITL_CHOICE_HELP: dict[HitlResolveChoice, str] = {
    HitlResolveChoice.ALLOW: "Run the tool now (same as SCHEDULE)",
    HitlResolveChoice.SCHEDULE: "Run the tool now",
    HitlResolveChoice.BLOCK: "Do not run; end the turn with the operator denial (optional steering is appended)",
    HitlResolveChoice.SKIP: "Do not run; optional steering text is sent as the tool result",
    HitlResolveChoice.TERMINATE: "Stop the agent run",
}


def format_hitl_choice_help(offered: list[HitlResolveChoice]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for choice in offered:
        if choice == HitlResolveChoice.SCHEDULE and HitlResolveChoice.ALLOW in offered:
            continue
        label = choice.value
        if label in seen:
            continue
        seen.add(label)
        help_text = HITL_CHOICE_HELP.get(choice, "")
        if help_text:
            lines.append(f"  {label}: {help_text}")
        else:
            lines.append(f"  {label}")
    return "\n".join(lines)
