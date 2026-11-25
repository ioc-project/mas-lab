#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Assembler injects text around list content using ContentPart."""

from __future__ import annotations

from mas.library.standard.plugins.context.assembler import ContextAssemblerPlugin
from mas.runtime.contracts.context_contract import ContextPart, ContextPlacement


def test_inject_system_prepends_text_part_to_list_content():
    plugin = ContextAssemblerPlugin()
    messages = [{"role": "system", "content": [{"type": "text", "text": "old"}]}]
    parts = [ContextPart(content="injected", placement=ContextPlacement.SYSTEM_HEADER)]
    out = plugin._inject_system(messages, parts)
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "injected" in content[0]["text"]
    assert content[-1] == {"type": "text", "text": "old"}


def test_on_pre_llm_call_skips_reinjection_when_system_list_already_assembled():
    import asyncio

    plugin = ContextAssemblerPlugin()
    plugin._always_reassemble = False
    marker = "<!-- ctx-assembled -->"
    messages = [{"role": "system", "content": [{"type": "text", "text": f"{marker}\nalready"}]}]
    out = asyncio.run(plugin.on_pre_llm_call({"messages": messages}))
    assert out["messages"][0]["content"][0]["text"].startswith(marker)


def test_inject_user_prepends_and_appends_text_parts():
    plugin = ContextAssemblerPlugin()
    messages = [{"role": "user", "content": [{"type": "text", "text": "body"}]}]
    prepend = [ContextPart(content="before", placement=ContextPlacement.USER_PREPEND)]
    append = [ContextPart(content="after", placement=ContextPlacement.USER_APPEND)]
    out = plugin._inject_user(messages, prepend, append)
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].endswith("before") or "before" in content[0]["text"]
    assert content[-1] == {"type": "text", "text": "after"}
    assert {"type": "text", "text": "body"} in content
