#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared HITL operator prompts — used by stdout console and curses TUI."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import HitlResolve


def print_hitl_brief(request: Any, *, err: TextIO | None = None) -> None:
    out = err or sys.stderr
    from mas.runtime.boundary.hitl.presentation import format_hitl_brief

    out.write("\n── HITL decision required ──\n")
    brief = format_hitl_brief(request)
    if brief.strip():
        out.write(brief + "\n")
    offered = list(request.offered_actions or list(HitlResolveChoice))
    labels = [c.value for c in offered]
    out.write(f"Enter choice ({', '.join(labels)}):\n")
    out.flush()


def build_hitl_resolve(
    request: Any,
    raw: str,
    *,
    operator_id: str = "cli-operator",
    read_line: Any | None = None,
) -> HitlResolve:
    offered = list(request.offered_actions or list(HitlResolveChoice))
    choice_raw = raw.strip().upper() or HitlResolveChoice.BLOCK.value
    try:
        choice = HitlResolveChoice(choice_raw)
    except ValueError:
        choice = HitlResolveChoice.BLOCK
    if choice not in offered:
        if choice == HitlResolveChoice.ALLOW and HitlResolveChoice.SCHEDULE in offered:
            choice = HitlResolveChoice.SCHEDULE
        else:
            choice = HitlResolveChoice.BLOCK
    ctx: dict = {"operator_id": operator_id}
    if choice in {HitlResolveChoice.SKIP, HitlResolveChoice.BLOCK} and read_line is not None:
        hook = (getattr(request, "context_data", None) or {}).get("hook", "")
        if choice == HitlResolveChoice.BLOCK:
            prompt = "Denial (optional, shown to the user): "
        elif hook == "ingress":
            prompt = "Steering (optional, replaces tool result): "
        else:
            prompt = "Steering (optional, synthetic tool result): "
        steering = read_line(prompt)
        if steering and steering.strip():
            ctx["steering"] = steering.strip()
    return HitlResolve(
        request_id=request.request_id,
        resolution=choice,
        operator_context=ctx,
    )
