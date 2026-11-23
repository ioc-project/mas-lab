#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One default-key policy: a provider's default secret goes only to its own default endpoint."""

from __future__ import annotations

import pytest
from mas.runtime.registry.llm_provider_registry import provider_key_ref
from mas.runtime.secrets import SecretRef, SecretRefError


class _Provider:
    kind = "acme"
    default_api_base = "https://api.acme.example/v1"
    default_secret = SecretRef(name="acme")


class _NoDefault:
    kind = "bare"
    default_api_base = "https://bare.example/v1"
    default_secret = None


def test_an_explicit_key_wins_for_any_host() -> None:
    ref = provider_key_ref({"secret": "mine"}, "https://elsewhere.example/v1", _Provider)
    assert ref == SecretRef(name="mine")


@pytest.mark.parametrize("base", ["", None, "https://api.acme.example/v1/", "https://API.acme.example:443/v1"])
def test_the_default_secret_goes_to_the_default_endpoint(base: object) -> None:
    assert provider_key_ref(None, base, _Provider) == SecretRef(name="acme")


@pytest.mark.parametrize(
    "base", ["https://evil.example/v1", "http://api.acme.example/v1", "https://api.acme.example:8443/v1"]
)
def test_another_origin_needs_its_own_key(base: str) -> None:
    # kills: reveal_secret_from's default, which sent the openai key to any api_base
    with pytest.raises(SecretRefError) as raised:
        provider_key_ref(None, base, _Provider, field="judge.api_key")
    text = str(raised.value)
    assert text.startswith("judge.api_key: ")
    assert "not the default endpoint of the acme provider" in text
    assert "optional: true" in text


def test_a_provider_without_a_default_secret_names_the_fix() -> None:
    with pytest.raises(SecretRefError, match="has no default secret"):
        provider_key_ref(None, "", _NoDefault)
