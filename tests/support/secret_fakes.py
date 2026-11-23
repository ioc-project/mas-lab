#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test-only secret helpers: an in-memory store, a context manager, the store conformance check."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Hashable

from mas.runtime.secrets.errors import SecretDenied, SecretMissing
from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.resolver import _PROCESS, SecretResolver, set_process_resolver
from mas.runtime.secrets.store import SecretInfo
from mas.runtime.secrets.value import SecretValue

CANARY = "s3cr3t-canary-9f3a"
PROVIDER_KEY = "sk-proj-" + ("Ab3_xY9-" * 19)[:150]  # the shape of a real provider key


class MemorySecretStore:
    """Dict-backed store: ``{name: {key: value}}``."""

    def __init__(self, data: Mapping[str, Mapping[str, str | bytes]] | None = None) -> None:
        self._data: dict[tuple[str, str], bytes] = {}
        self._tokens: dict[tuple[str, str], int] = {}
        for name, keys in (data or {}).items():
            for key, value in keys.items():
                self.put(name, value, key=key)

    def put(self, name: str, value: str | bytes, *, key: str = "value") -> None:
        self._data[(name, key)] = value if isinstance(value, bytes) else str(value).encode("utf-8")
        self._tokens[(name, key)] = self._tokens.get((name, key), 0) + 1

    def get(self, ref: SecretRef) -> SecretValue:
        if (ref.name, ref.key) not in self._data:
            raise SecretMissing(ref)
        return SecretValue(self._data[(ref.name, ref.key)], name=ref.name)

    def describe(self, ref: SecretRef) -> SecretInfo:
        raw = self._data.get((ref.name, ref.key))
        return SecretInfo(present=raw is not None, size=len(raw or b""))

    def change_token(self, ref: SecretRef) -> Hashable:
        return self._tokens.get((ref.name, ref.key), 0)


@contextmanager
def bound_secrets(mapping: Mapping[str, str] | None = None, *, ttl_s: float = 30.0) -> Iterator[SecretResolver]:
    """Make a MemorySecretStore of ``name → value`` (key ``value``) the process resolver for the block."""
    saved = list(_PROCESS)
    resolver = SecretResolver(MemorySecretStore({n: {"value": v} for n, v in (mapping or {}).items()}), ttl_s=ttl_s)
    set_process_resolver(resolver)
    try:
        yield resolver
    finally:
        _PROCESS[:] = saved


def check_secret_store(store: Any, *, put: Any, rotate: Any) -> None:
    """Present, missing, describe and rotation; messages never hold the canary."""
    ref, missing = SecretRef(name="kit"), SecretRef(name="kit", key="missing-key")
    put(ref.name, ref.key, CANARY)
    value = store.get(ref)
    assert value.reveal() == CANARY and CANARY not in str(value) and CANARY not in repr(value)
    info = store.describe(ref)
    assert info.present is True and info.size == len(CANARY.encode("utf-8"))
    try:
        store.get(missing)
    except (SecretMissing, SecretDenied) as exc:
        assert CANARY not in str(exc) and CANARY not in repr(exc.args)
    else:
        raise AssertionError(f"{type(store).__name__}.get did not raise for a missing ref")
    absent = store.describe(missing)
    assert absent.present is False and absent.size == 0
    rotate(ref.name, ref.key, f"{CANARY}-rotated")
    assert store.get(ref).reveal() == f"{CANARY}-rotated"
