#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The live engine gates media against the catalog before any provider call."""

from __future__ import annotations

import asyncio

import pytest
from mas.runtime.contracts.content_types import ModalityNotSupported
from mas.runtime.engine.llm_live import LiveLlmEngine

_IMAGE = [
    {"type": "text", "text": "see"},
    {
        "type": "image",
        "media_type": "image/png",
        "sha256": "abc",
        "name": "chart.png",
        "bytes": 4,
    },
]


class _Spy:
    def __init__(self) -> None:
        self.calls = 0
        self.messages: object = None

    async def achat_completion(self, **kwargs: object) -> dict[str, str]:
        self.calls += 1
        self.messages = kwargs.get("messages")
        return {"role": "assistant", "content": "no", "finish_reason": "stop"}


def test_engine_refuses_image_on_text_only_catalog_model_before_provider() -> None:
    spy = _Spy()
    engine = LiveLlmEngine(llm_provider=spy, use_cache=False, model="gemma-4-26b", stream=False)
    with pytest.raises(ModalityNotSupported, match="gemma"):
        asyncio.run(engine._achat_completion([{"role": "user", "content": _IMAGE}], tools=None))
    assert spy.calls == 0


def test_engine_allows_image_on_gpt_4o() -> None:
    spy = _Spy()
    engine = LiveLlmEngine(llm_provider=spy, use_cache=False, model="gpt-4o", stream=False)
    out = asyncio.run(engine._achat_completion([{"role": "user", "content": _IMAGE}], tools=None))
    assert spy.calls == 1
    assert out["content"] == "no"
    assert spy.messages[0]["content"][1]["type"] == "image"
