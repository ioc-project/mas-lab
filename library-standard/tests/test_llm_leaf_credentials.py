#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each LLMProxy leaf uses its own host and API key env."""

from __future__ import annotations

from fake_openai import FakeOpenAI, text
from mas.runtime.registry.llm_provider_registry import llm_providers_from_manifest
from secret_fakes import bound_secrets


async def test_leaf_call_uses_that_leaf_api_key(monkeypatch) -> None:
    with (
        FakeOpenAI(lambda _body: text("from-arm")) as arm,
        FakeOpenAI(lambda _body: text("from-judge")) as judge,
        bound_secrets({"arm": "key-for-A", "judge": "key-for-B"}),
    ):
        provider = llm_providers_from_manifest(
            None,
            llm_proxies={
                "arm": {"api_base": arm.url, "api_key": {"secret": "arm"}, "allowed": ["m-a"], "protocol": "openai"},
                "judge": {
                    "api_base": judge.url,
                    "api_key": {"secret": "judge"},
                    "allowed": ["m-b"],
                    "protocol": "openai",
                },
            },
        )
        out = await provider.achat_completion(model="m-b", messages=[{"role": "user", "content": "hi"}])
    assert out["content"] == "from-judge"
    assert judge.authorizations() == ["Bearer key-for-B"]
    assert arm.authorizations() == []
