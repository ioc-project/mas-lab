#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The shared OpenAI client factory: the key is revealed at use and goes only where the rule allows."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fake_openai import FakeOpenAI, text
from mas.library.standard.lib.openai_client import (
    openai_api_base,
    openai_client_for_key,
    reveal_openai_key,
)
from mas.runtime.secrets import SecretRef, SecretRefError
from mas.runtime.secrets.transport import PlainHttpRefused
from secret_fakes import CANARY, bound_secrets


def _call(client: Any) -> str:
    reply = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    return str(reply.choices[0].message.content)


def test_a_secret_ref_is_revealed_and_sent_to_a_loopback_endpoint() -> None:
    with FakeOpenAI(lambda _body: text("ok")) as fake, bound_secrets({"gw-key": CANARY}):
        client = openai_client_for_key({"secret": "gw-key"}, fake.url)
        assert _call(client) == "ok"
    assert fake.authorizations() == [f"Bearer {CANARY}"]


def test_an_optional_ref_that_is_not_set_sends_no_auth_header() -> None:
    with FakeOpenAI(lambda _body: text("ok")) as fake, bound_secrets({}):
        client = openai_client_for_key({"secret": {"name": "gw-key", "optional": True}}, fake.url)
        assert _call(client) == "ok"
    assert fake.authorizations() == [None]


def test_the_default_secret_stays_on_the_default_endpoint() -> None:
    assert openai_api_base(None).startswith("https://")
    assert reveal_openai_key(None, None) == "secret"
    with pytest.raises(SecretRefError, match="not the default endpoint"):
        reveal_openai_key(None, "https://gw.example/v1")


def test_a_key_is_refused_over_plain_http_to_another_host() -> None:
    with bound_secrets({"gw-key": CANARY}), pytest.raises(PlainHttpRefused) as refused:
        reveal_openai_key(SecretRef("gw-key"), "http://gw.example/v1")
    assert CANARY not in str(refused.value)


def test_a_missing_sdk_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match="pip install openai"):
        openai_client_for_key(SecretRef("openai"), "https://api.openai.com/v1")
