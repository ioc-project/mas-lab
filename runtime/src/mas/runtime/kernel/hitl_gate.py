#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL pause emission — shared by egress and ingress governance gates."""

from __future__ import annotations

from mas.runtime.boundary.hitl.presentation import enrich_hitl_request, tool_result_hitl_context
from mas.runtime.kernel.coord_hook import coord_on_egress_hitl
from mas.runtime.kernel.coupling import apply_gov_hitl_hold
from mas.runtime.kernel.state import QProduct, ScheduledEgress
from mas.runtime.schema.egress import EgressSymbol, EmitHitlRequest
from mas.runtime.schema.hitl import HitlQuestionType, HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn


def emit_egress_hitl_pause(
    q: QProduct,
    *,
    cid: int,
    op: ScheduledEgress,
    question: str,
    question_type: HitlQuestionType,
    offered_actions: list[HitlResolveChoice],
    context_data: dict,
    call_id: str = "",
) -> list[EgressSymbol]:
    apply_gov_hitl_hold(
        q,
        request_id=cid,
        call_id=call_id,
        op=op,
        question_type=question_type.value,
    )
    coord_on_egress_hitl(q)
    return [
        enrich_hitl_request(
            EmitHitlRequest(
                request_id=cid,
                question_type=question_type,
                question=question,
                choices=[a.value.title() for a in offered_actions],
                pending_schedule=op,
                offered_actions=offered_actions,
                context_data=context_data,
            ),
            q=q,
        )
    ]


def emit_ingress_hitl_pause(
    q: QProduct,
    *,
    cid: int,
    event: EngineIoReturn,
    offered_actions: list[HitlResolveChoice] | None = None,
) -> list[EgressSymbol]:
    from mas.runtime.machines.gov import gov_enter_hitl_pending

    actions = offered_actions or [
        HitlResolveChoice.ALLOW,
        HitlResolveChoice.BLOCK,
        HitlResolveChoice.SKIP,
    ]
    # The result of a batch call names its own tool; the last pending name is only right for a single call.
    by_cid = q.pending_tools_by_cid.get(event.correlation_id)
    tool_name = by_cid[0] if by_cid is not None else q.pending_tool_name
    gov_enter_hitl_pending(
        q,
        request_id=cid,
        pending_schedule="NONE",
        question_type=HitlQuestionType.CONFIRM.value,
        tool_name=tool_name,
        ingress_return=event.model_dump(mode="json"),
    )
    ctx = tool_result_hitl_context(q, event, tool_name=tool_name)
    # User-facing "context"; kernel commits to working memory on ALLOW/steer.
    return [
        enrich_hitl_request(
            EmitHitlRequest(
                request_id=cid,
                question_type=HitlQuestionType.CONFIRM,
                question="Include tool result in context?",
                choices=[a.value.title() for a in actions],
                pending_schedule="NONE",
                offered_actions=actions,
                context_data=ctx,
            ),
            q=q,
        )
    ]
