#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern context injection — TLA: ContextMachine.tla / M_ctx."""

from __future__ import annotations

from typing import Any

from mas.runtime.kernel.state import QProduct
from mas.runtime.registry import get_registry


def dp_protocol_lines(pattern_plugin_id: str, q: QProduct | None = None) -> list[str]:
    """The pattern's protocol lines for the phase ``q`` is in now (empty if none)."""
    info = get_registry().get("design_pattern", pattern_plugin_id)
    if info is None:
        return []
    plugin = info.load_class()()
    lines_fn = getattr(plugin, "protocol_lines", None)
    if not callable(lines_fn):
        return []
    return list(lines_fn(q) or [])


def inject_dp_protocol(
    injected: list[str],
    *,
    pattern_plugin_id: str,
    q: QProduct | None = None,
) -> list[str]:
    """``injected`` followed by the pattern's protocol lines; ``injected`` is not mutated."""
    extra = dp_protocol_lines(pattern_plugin_id, q)
    if not extra:
        return injected
    return [*injected, *extra]


def system_context_lines(ctx: Any) -> list[str]:
    """System-prompt lines for one LLM call: the injected context, then the
    pattern's protocol lines for its current phase.

    Computed on every call rather than stored in ``injected_context``, so a
    pattern that changes phase mid-turn (plan → synthesis) is seen with the new
    phase's lines, and the lines never repeat across turns.
    """
    injected = list(getattr(ctx, "injected_context", None) or [])
    pattern_plugin_id = str(getattr(ctx, "pattern_plugin_id", "") or "")
    if not pattern_plugin_id:
        return injected
    return inject_dp_protocol(injected, pattern_plugin_id=pattern_plugin_id, q=getattr(ctx, "q_product", None))
