#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The LLM judges take api_base from the constructor, not the host environment."""

from __future__ import annotations

import openai
import pytest
from mas.library.eval.providers.adversarial import AdversarialProvider
from mas.library.eval.providers.trip_planner_gt import TripPlannerGTProvider
from secret_fakes import bound_secrets


@pytest.mark.parametrize("provider_cls", [AdversarialProvider, TripPlannerGTProvider])
def test_judge_base_url_order(monkeypatch, provider_cls) -> None:
    seen: list[str | None] = []

    class _Client:
        def __init__(self, *, api_key: object, base_url: str | None = None) -> None:
            seen.append(base_url)

    monkeypatch.setattr(openai, "OpenAI", _Client)
    with bound_secrets({"judge": "test-key"}):
        provider_cls(api_base="https://old/v1", api_key={"secret": "judge"})._get_llm_client()
        provider_cls(api_base="https://new/v1", api_key={"secret": "judge"})._get_llm_client()
    assert seen == ["https://old/v1", "https://new/v1"]
