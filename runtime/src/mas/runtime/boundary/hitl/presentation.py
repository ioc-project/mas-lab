#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL decision brief — present governance context to operators."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mas.runtime.boundary.hitl.choices import format_hitl_choice_help
from mas.runtime.schema.egress import EmitHitlRequest

if TYPE_CHECKING:
    from mas.runtime.kernel.state import QProduct


def tool_hitl_context(q: QProduct, *, user_text: str = "", pending_schedule: str = "") -> dict:
    """Structured payload for operator review before tool execution."""
    ctx: dict = {
        "pending_schedule": pending_schedule or q.scheduled_egress,
        "tool_name": q.pending_tool_name or "",
        "tool_arguments": dict(q.pending_tool_args or {}),
        "hook": "egress",
    }
    if user_text:
        ctx["user_message"] = user_text
    return ctx


def tool_result_hitl_context(q: QProduct, event: object, *, tool_name: str = "") -> dict:
    """Structured payload for operator review before tool result enters context."""
    from mas.runtime.schema.ingress import EngineIoReturn

    ctx: dict = {
        "hook": "ingress",
        "response_kind": getattr(event, "response_kind", ""),
        "correlation_id": getattr(event, "correlation_id", 0),
        "tool_name": tool_name or q.pending_tool_name or "",
    }
    if isinstance(event, EngineIoReturn) and event.text:
        preview = event.text
        if len(preview) > 500:
            preview = preview[:497] + "..."
        ctx["tool_result_preview"] = preview
    return ctx


def enrich_hitl_request(
    request: EmitHitlRequest,
    *,
    q: QProduct | None = None,
    user_text: str = "",
) -> EmitHitlRequest:
    """Merge tool / turn context into an egress or ingress HITL request."""
    data = dict(request.context_data or {})
    if q is not None and data.get("hook") != "ingress":
        data.update(tool_hitl_context(q, user_text=user_text, pending_schedule=request.pending_schedule))
    tool = data.get("tool_name") or data.get("tool") or data.get("op", "")
    question = request.question
    if data.get("hook") == "ingress":
        if question in ("", "Include tool result in context?", "Accept tool result into context?"):
            question = "Include tool result in context?"
    elif tool and question in ("", "Approve tool call?", f"Approve {data.get('op')}?"):
        question = f"Allow tool `{tool}` to run?"
    return request.model_copy(
        update={
            "question": question,
            "context_data": data,
        }
    )


def format_hitl_brief(request: EmitHitlRequest) -> str:
    """Human-readable decision brief for CLI / TUI."""
    lines = [
        f"request_id={request.request_id}  type={request.question_type.value}",
        f"question: {request.question}",
    ]
    data = request.context_data or {}
    if data.get("user_message"):
        lines.append(f"user: {data['user_message']}")
    if data.get("tool_name"):
        lines.append(f"tool: {data['tool_name']}")
    if data.get("tool_arguments"):
        try:
            args = json.dumps(data["tool_arguments"], ensure_ascii=False)
        except TypeError:
            args = str(data["tool_arguments"])
        lines.append(f"arguments: {args}")
    if data.get("tool_result_preview"):
        lines.append(f"result: {data['tool_result_preview']}")
    if data.get("pending_schedule") and data.get("pending_schedule") != "NONE":
        lines.append(f"schedule: {data['pending_schedule']}")
    if data.get("destructive") is not None:
        lines.append(f"destructive: {data['destructive']}")
    offered = ", ".join(a.value for a in (request.offered_actions or []))
    if offered:
        lines.append(f"choices: {offered}")
        help_block = format_hitl_choice_help(list(request.offered_actions or []))
        if help_block.strip():
            lines.append(help_block)
    return "\n".join(lines)
