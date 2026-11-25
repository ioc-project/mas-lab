#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Semantic memory leaves a list user message intact and queries its text view."""

from __future__ import annotations

from typing import Any

from mas.library.standard.plugins.context.summarizer import _text_view
from mas.library.standard.plugins.memory.memory_semantic import SemanticMemoryPlugin


def test_memory_prepends_text_part_and_does_not_mutate_the_list() -> None:
    original = [
        {"type": "text", "text": "see this"},
        {
            "type": "image",
            "media_type": "image/png",
            "sha256": "abc",
            "name": "chart.png",
            "bytes": 4,
        },
    ]
    held = list(original)
    messages: list[dict[str, Any]] = [{"role": "user", "content": original}]
    plugin = SemanticMemoryPlugin()
    plugin._context_inject = True
    plugin._pending_query = ""
    plugin.search = lambda query, max_results=3: [  # type: ignore[method-assign]
        {"text": "remembered fact", "score": 0.9}
    ]
    plugin._ensure_initialized = lambda: None  # type: ignore[method-assign]
    out = plugin.on_pre_llm_call({"messages": messages})
    content = out["messages"][0]["content"]
    assert isinstance(content, list)
    assert content is not original
    assert original == held
    assert content[0]["type"] == "text"
    assert "remembered fact" in content[0]["text"]
    assert content[1:] == original


def test_write_memory_and_transcript_use_text_of_list_content() -> None:
    plugin = SemanticMemoryPlugin()
    seen: dict[str, Any] = {}

    def _index(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "doc-1"

    plugin.index_document = _index  # type: ignore[method-assign]
    out = plugin.write_memory(
        "semantic",
        {
            "content": [
                {"type": "text", "text": "see"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "sha256": "abc",
                    "name": "chart.png",
                    "bytes": 4,
                },
            ]
        },
    )
    assert out["key"] == "doc-1"
    assert seen["content"] == "see"

    plugin.index_session_transcript(
        "clerk",
        "s1",
        [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
    )
    assert "hello" in seen["content"]


def test_chunk_text_skips_whitespace_only_segments() -> None:
    from mas.library.standard.plugins.memory.memory_semantic import _chunk_text

    chunks = _chunk_text("    hello", chunk_tokens=1, overlap_tokens=0)
    assert chunks
    assert all(c["text"].strip() for c in chunks)


def test_collect_context_skips_when_search_fails() -> None:
    plugin = SemanticMemoryPlugin()
    plugin._context_inject = True
    plugin._pending_query = "q"
    plugin.search = lambda query: (_ for _ in ()).throw(RuntimeError("down"))  # type: ignore[method-assign]
    assert plugin.collect_context() == []


def test_summarizer_text_view_uses_placeholders_and_no_media() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "sha256": "abc",
                    "name": "chart.png",
                    "bytes": 4,
                },
            ],
        }
    ]
    view = _text_view(messages)
    assert "see" in view
    assert "[image chart.png]" in view
    assert "abc" not in view
    assert "data:" not in view
