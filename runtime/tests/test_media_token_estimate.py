#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Text-only token estimates stay the old numbers; media adds the documented constant."""

from __future__ import annotations

from mas.library.standard.plugins.context.conversation import SummarizingConversation
from mas.library.standard.plugins.context.token_budget import estimate_tokens
from mas.runtime.contracts.content_types import MEDIA_PART_TOKEN_ESTIMATE, token_estimate_for_content
from mas.runtime.engine.llm_output_limits import estimate_prompt_tokens

_HELLO = [{"role": "user", "content": "hello"}]
_MEDIA = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "hello"},
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


def test_text_only_estimates_match_char_over_four():
    # "hello" is 5 chars → 1 token plus 4 per message.
    assert estimate_tokens(_HELLO) == 5
    assert estimate_prompt_tokens(_HELLO) == 5
    assert SummarizingConversation._estimate_tokens(_HELLO) == 5
    assert token_estimate_for_content("hello") == 5 // 4


def test_media_message_adds_the_constant():
    assert token_estimate_for_content(_MEDIA[0]["content"]) == 5 // 4 + MEDIA_PART_TOKEN_ESTIMATE
    assert estimate_tokens(_MEDIA) == estimate_tokens(_HELLO) + MEDIA_PART_TOKEN_ESTIMATE
    assert estimate_prompt_tokens(_MEDIA) == estimate_prompt_tokens(_HELLO) + MEDIA_PART_TOKEN_ESTIMATE
    assert SummarizingConversation._estimate_tokens(_MEDIA) == (
        SummarizingConversation._estimate_tokens(_HELLO) + MEDIA_PART_TOKEN_ESTIMATE
    )
