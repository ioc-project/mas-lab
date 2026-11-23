#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolver TTL, rotation, NullResolver, prefix."""

from __future__ import annotations

import pytest
from mas.runtime.secrets.errors import SecretMissing
from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.resolver import NullResolver, SecretResolver, current_resolver
from secret_fakes import CANARY, MemorySecretStore, bound_secrets


@pytest.mark.no_secrets
def test_null_resolver_raises_missing() -> None:
    ref = SecretRef(name="openai")
    with pytest.raises(SecretMissing, match="secret:openai/value"):
        NullResolver().reveal(ref)
    assert "s3cr" not in str(SecretMissing(ref))
    assert current_resolver().describe(ref).present is False


def test_reveal_adds_the_prefix() -> None:
    resolver = SecretResolver(MemorySecretStore({"mcp": {"bearer": "tok"}}), ttl_s=30)
    assert resolver.reveal(SecretRef(name="mcp", key="bearer", prefix="Bearer ")) == "Bearer tok"


def test_ttl_cache_invalidates_on_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemorySecretStore({"openai": {"value": CANARY}})
    resolver = SecretResolver(store, ttl_s=30)
    ref = SecretRef(name="openai")
    clock = {"now": 0.0}
    monkeypatch.setattr("mas.runtime.secrets.resolver.time.monotonic", lambda: clock["now"])
    assert resolver.reveal(ref) == CANARY
    store.put("openai", "rotated")
    clock["now"] = 1.0
    assert resolver.reveal(ref) == "rotated"
    store.put("openai", "again")
    clock["now"] = 1.5
    assert resolver.reveal(ref) == "again"


def test_reveal_mapping_refuses_a_secret_value_and_non_strings() -> None:
    from mas.runtime.secrets.resolver import reveal_mapping
    from mas.runtime.secrets.value import SecretValue

    with bound_secrets({"openai": CANARY}) as resolver:
        with pytest.raises(TypeError, match="Authorization") as caught:
            reveal_mapping({"Authorization": SecretValue(CANARY, name="openai")}, resolver=resolver)
        assert CANARY not in str(caught.value)
        with pytest.raises(TypeError, match="PORT"):
            reveal_mapping({"PORT": 8080}, resolver=resolver)
        with pytest.raises(TypeError, match="X"):
            reveal_mapping({"X": ["a"]}, resolver=resolver)


def test_an_optional_missing_ref_omits_the_entry() -> None:
    """Consumer-agnostic: an optional ref that the store lacks drops its header or env key."""
    from mas.runtime.secrets.resolver import reveal_mapping

    with bound_secrets({"openai": "k"}) as resolver:
        raw = {
            "Authorization": SecretRef(name="openai", prefix="Bearer "),
            "X-Api-Key": {"secret": {"name": "absent", "optional": True}},
            "Accept": "application/json",
        }
        assert reveal_mapping(raw, resolver=resolver) == {"Authorization": "Bearer k", "Accept": "application/json"}
        with pytest.raises(SecretMissing, match="absent/value"):
            reveal_mapping({"X-Api-Key": SecretRef(name="absent")}, resolver=resolver)


def test_cache_drops_a_deleted_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemorySecretStore({"openai": {"value": "k"}})
    resolver = SecretResolver(store, ttl_s=300)
    ref = SecretRef("openai")
    assert resolver.reveal(ref) == "k"
    monkeypatch.setattr(store, "change_token", lambda r: None)
    monkeypatch.setattr(store, "get", lambda r: (_ for _ in ()).throw(SecretMissing(r)))
    with pytest.raises(SecretMissing):
        resolver.reveal(ref)


def test_every_fetch_evicts_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemorySecretStore({"a": {"value": "A"}, "b": {"value": "B"}})
    resolver = SecretResolver(store, ttl_s=10)
    clock = {"now": 0.0}
    monkeypatch.setattr("mas.runtime.secrets.resolver.time.monotonic", lambda: clock["now"])
    resolver.reveal(SecretRef("a"))
    clock["now"] = 5.0
    resolver.reveal(SecretRef("b"))
    assert set(resolver._cache) == {("a", "value"), ("b", "value")}
    clock["now"] = 11.0
    resolver.reveal(SecretRef("b"))  # served from the cache; ``a`` is past its TTL and goes
    assert set(resolver._cache) == {("b", "value")}


def test_cache_refetches_after_the_bounded_ttl_even_with_the_same_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.runtime.secrets.resolver import MAX_TTL_S

    store = MemorySecretStore({"openai": {"value": "k"}})
    fetches: list[str] = []
    real_get = store.get

    def counting_get(ref: SecretRef):
        fetches.append(ref.name)
        return real_get(ref)

    monkeypatch.setattr(store, "get", counting_get)
    clock = {"now": 0.0}
    monkeypatch.setattr("mas.runtime.secrets.resolver.time.monotonic", lambda: clock["now"])
    resolver = SecretResolver(store, ttl_s=10**9)  # capped at MAX_TTL_S
    ref = SecretRef("openai")
    resolver.reveal(ref)
    clock["now"] = MAX_TTL_S - 1
    resolver.reveal(ref)
    assert len(fetches) == 1  # served from the cache inside the TTL
    clock["now"] = MAX_TTL_S + 1
    resolver.reveal(ref)
    assert len(fetches) == 2  # same change token, TTL counted from the fetch: fetched again
