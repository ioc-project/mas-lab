#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The LLM judges follow OPENAI_BASE_URL, then the older OPENAI_API_BASE."""

from __future__ import annotations

import openai
import pytest
from mas.library.eval.providers.adversarial import AdversarialProvider
from mas.library.eval.providers.trip_planner_gt import TripPlannerGTProvider


@pytest.mark.parametrize("provider_cls", [AdversarialProvider, TripPlannerGTProvider])
def test_judge_base_url_order(monkeypatch, provider_cls) -> None:
    seen: list[str | None] = []

    class _Client:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            seen.append(base_url)

    monkeypatch.setattr(openai, "OpenAI", _Client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    monkeypatch.setenv("OPENAI_API_BASE", "http://old/v1")
    provider_cls()._get_llm_client()
    monkeypatch.setenv("OPENAI_BASE_URL", "http://new/v1")
    provider_cls()._get_llm_client()
    assert seen == ["http://old/v1", "http://new/v1"]
