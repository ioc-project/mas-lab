#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL presentation and operator choice integration."""

from mas.runtime.boundary.hitl.choices import format_hitl_choice_help
from mas.runtime.boundary.hitl.presentation import tool_hitl_context
from mas.runtime.kernel.state import QProduct
from mas.runtime.schema.hitl import HitlResolveChoice


def test_format_hitl_choice_help_dedupes_allow_schedule():
    text = format_hitl_choice_help([HitlResolveChoice.ALLOW, HitlResolveChoice.SCHEDULE, HitlResolveChoice.BLOCK])
    assert "ALLOW" in text or "SCHEDULE" in text
    assert "BLOCK" in text
    assert text.count("Run the tool now") <= 1


def test_tool_hitl_context_includes_pending_tool():
    q = QProduct()
    q.pending_tool_name = "web_search"
    q.pending_tool_args = {"q": "paris"}
    ctx = tool_hitl_context(q, user_text="find hotels")
    assert ctx["tool_name"] == "web_search"
    assert ctx["user_message"] == "find hotels"
