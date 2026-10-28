#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Helpers around the runtime ``SendContext`` envelope."""

from __future__ import annotations

from collections.abc import Mapping

from mas.runtime.boundary.agentcomm.protocol import SendContext


def merge_send_context(
    headers: Mapping[str, str] | None,
    context: SendContext | None,
    *,
    sender: str | None = None,
) -> SendContext:
    """Build a new envelope. Explicit ``headers`` replace ``context.headers``.

    Merge (keep ``context.headers``) only when ``headers`` is ``None``.
    ``sender`` replaces any caller value. Omit ``sender`` to keep the value
    from ``context`` (empty if there is none).
    """
    if headers is not None:
        outgoing = dict(headers)
    elif context is not None:
        outgoing = dict(context.headers)
    else:
        outgoing = {}
    intent = context.intent if context is not None else ""
    mode = context.mode if context is not None else ""
    if sender is None:
        sender = context.sender if context is not None else ""
    return SendContext(headers=outgoing, intent=intent, sender=sender, mode=mode)


def align_send(
    headers: Mapping[str, str] | None,
    context: SendContext | None,
    *,
    sender: str | None = None,
) -> tuple[dict[str, str], SendContext]:
    """One envelope. Explicit ``headers`` replace; otherwise keep ``context.headers``."""
    ctx = merge_send_context(headers, context, sender=sender)
    outgoing = dict(ctx.headers)
    return outgoing, SendContext(
        headers=outgoing, intent=ctx.intent, sender=ctx.sender, mode=ctx.mode
    )
