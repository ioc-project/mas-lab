#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The judges follow the rule of the LLM layers: the ``openai`` default secret goes only to the OpenAI
endpoint. Another host needs its own ``api_key``; an optional one that is not set sends no auth header."""

from __future__ import annotations

from collections.abc import Iterator

import openai
import pytest
from fake_openai import FakeOpenAI, text
from mas.library.eval.providers.adversarial import AdversarialProvider
from mas.library.eval.providers.trip_planner_gt import TripPlannerGTProvider
from mas.library.standard.lib.openai_client import openai_client_for_key
from mas.runtime.secrets import SecretRefError
from secret_fakes import bound_secrets

OPENAI_KEY = "sk-OPENAI-CANARY"


@pytest.fixture
def server() -> Iterator[FakeOpenAI]:
    with FakeOpenAI(lambda _body: text('{"score": 1}')) as fake:
        yield fake


@pytest.mark.parametrize("cls", [AdversarialProvider, TripPlannerGTProvider])
def test_a_judge_on_another_host_without_a_key_does_not_get_the_openai_secret(cls, server) -> None:
    with bound_secrets({"openai": OPENAI_KEY}):
        with pytest.raises(SecretRefError, match=r"api_key: \{secret: NAME\}") as info:
            cls(api_base=server.url)._llm_judge("x")
    assert server.headers == []
    assert OPENAI_KEY not in str(info.value) and "optional: true" in str(info.value)


@pytest.mark.parametrize("cls", [AdversarialProvider, TripPlannerGTProvider])
def test_a_judge_on_the_openai_endpoint_uses_the_openai_secret(monkeypatch, cls) -> None:
    seen: list[dict] = []

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            seen.append(kwargs)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://elsewhere.example/v1")
    monkeypatch.setattr(openai, "OpenAI", _Client)
    with bound_secrets({"openai": OPENAI_KEY}):
        cls()._get_llm_client()
        cls(api_base="https://api.openai.com/v1/")._get_llm_client()
    # The base URL is always given: the SDK would otherwise read OPENAI_BASE_URL.
    assert [k["base_url"] for k in seen] == ["https://api.openai.com/v1", "https://api.openai.com/v1/"]
    assert [k["api_key"] for k in seen] == [OPENAI_KEY, OPENAI_KEY]


def test_an_optional_key_that_is_not_set_sends_no_auth_header(server) -> None:
    with bound_secrets({}):
        provider = AdversarialProvider(api_base=server.url, api_key={"secret": {"name": "local", "optional": True}})
        assert provider._llm_judge("x")["score"] == 1.0
    assert server.authorizations() == [None]


def test_a_required_key_that_is_not_set_is_refused_by_name() -> None:
    from mas.runtime.secrets import SecretMissing

    with bound_secrets({}), pytest.raises(SecretMissing, match="local"):
        openai_client_for_key({"secret": "local"}, "http://127.0.0.1:1/v1")
