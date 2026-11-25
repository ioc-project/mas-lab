#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Message content that is a part list is never stringified as a Python repr."""

from __future__ import annotations

from mas.runtime.contracts.content_types import content_fingerprint
from mas.runtime.engine.exchange_preview import format_llm_messages
from mas.runtime.engine.textual_tool_calls import recover_tool_calls_from_content

_IMAGE = {
    "type": "image",
    "media_type": "image/png",
    "sha256": "abc",
    "name": "chart.png",
    "bytes": 4,
}
_LIST = [{"type": "text", "text": "see this"}, _IMAGE]


def test_recover_tool_calls_reads_text_parts_not_the_list_repr():
    content = [{"type": "text", "text": "call: echo(x:1)"}]
    tool_calls, _cleaned = recover_tool_calls_from_content(content, known_tool_names={"echo"})
    assert [item["function"]["name"] for item in tool_calls] == ["echo"]


def test_format_llm_messages_tool_role_does_not_dump_a_list():
    preview = format_llm_messages(
        [{"role": "tool", "tool_call_id": "t1", "content": [{"type": "text", "text": "pong"}]}]
    )
    assert "pong" in preview
    assert "[{'type'" not in preview


def test_content_fingerprint_is_stable_json_for_parts():
    a = content_fingerprint(_LIST)
    b = content_fingerprint(list(_LIST))
    assert a == b
    assert a != content_fingerprint("see this")
    assert '"sha256"' in a and "abc" in a


def test_content_fingerprint_distinguishes_two_images_with_the_same_caption():
    a = [
        {"type": "text", "text": "see"},
        {"type": "image", "media_type": "image/png", "sha256": "aaa", "name": "a.png", "bytes": 1},
    ]
    b = [
        {"type": "text", "text": "see"},
        {"type": "image", "media_type": "image/png", "sha256": "bbb", "name": "a.png", "bytes": 1},
    ]
    assert content_fingerprint(a) != content_fingerprint(b)
