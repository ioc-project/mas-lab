#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL prompt: BLOCK and SKIP both collect optional steering."""

from __future__ import annotations

from mas.ctl.session.hitl_prompt import build_hitl_resolve
from mas.runtime.schema.hitl import HitlRequest, HitlResolveChoice


def _request(**kwargs) -> HitlRequest:
    return HitlRequest(
        request_id=1,
        offered_actions=[
            HitlResolveChoice.ALLOW,
            HitlResolveChoice.BLOCK,
            HitlResolveChoice.SKIP,
        ],
        **kwargs,
    )


def test_block_prompts_for_denial_steering() -> None:
    prompts: list[str] = []

    def read_line(prompt: str) -> str:
        prompts.append(prompt)
        return "Budget work is out of policy."

    event = build_hitl_resolve(_request(), "BLOCK", read_line=read_line)
    assert event.resolution == HitlResolveChoice.BLOCK
    assert event.steering_text == "Budget work is out of policy."
    assert prompts == ["Denial (optional, shown to the user): "]


def test_skip_still_prompts_for_tool_result_steering() -> None:
    event = build_hitl_resolve(
        _request(context_data={"hook": "ingress"}),
        "SKIP",
        read_line=lambda prompt: "Ask the user instead.",
    )
    assert event.resolution == HitlResolveChoice.SKIP
    assert event.steering_text == "Ask the user instead."
