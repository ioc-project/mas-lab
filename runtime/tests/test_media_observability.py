#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Media runs never put a data URL or a base64 blob into events or previews."""

from __future__ import annotations

import json

from mas.runtime.engine.exchange_preview import format_llm_messages
from mas.runtime.engine.llm_cache import llm_cache_key

_CONTENT = [
    {"type": "text", "text": "What is the trend?"},
    {
        "type": "image",
        "media_type": "image/png",
        "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "name": "chart.png",
        "bytes": 12,
    },
]
_MESSAGES = [{"role": "user", "content": _CONTENT}]


def test_preview_and_cache_key_have_no_data_url_or_base64() -> None:
    preview = format_llm_messages(_MESSAGES)
    key = llm_cache_key("gpt-4o", _MESSAGES, None)
    blob = json.dumps({"preview": preview, "key": key, "messages": _MESSAGES})
    assert "data:" not in blob
    assert "base64" not in blob
    assert "data:image" not in preview
    assert "sha256:aaaaaaaaaaaa" in preview
    assert "What is the trend?" in preview


def test_user_input_event_lists_media_without_bytes() -> None:
    from mas.runtime.contracts.content_types import media_parts

    listed = media_parts(_CONTENT)
    rec = {
        "text": "What is the trend?",
        "media": [
            {
                "sha256": part.get("sha256"),
                "media_type": part.get("media_type"),
                "name": part.get("name"),
                "bytes": part.get("bytes"),
            }
            for part in listed
        ],
    }
    dumped = json.dumps(rec)
    assert rec["media"][0]["name"] == "chart.png"
    assert rec["media"][0]["sha256"].startswith("aaaa")
    assert "data:" not in dumped
    assert "base64" not in dumped
