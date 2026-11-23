#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SecretResolver: point-of-use fetch with a short TTL cache."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any, Hashable

from mas.runtime.secrets.errors import SecretMissing
from mas.runtime.secrets.ref import SecretRef, is_ref, parse_secret_ref
from mas.runtime.secrets.store import SecretInfo, SecretStore
from mas.runtime.secrets.value import SecretValue

_PROCESS: list[SecretResolver] = []
MAX_TTL_S = 300.0


class SecretResolver:
    """What plugins receive. Fetches through the store at reveal time."""

    def __init__(self, store: SecretStore, *, ttl_s: float = 30.0) -> None:
        self._store = store
        self._ttl_s = min(max(float(ttl_s), 0.0), MAX_TTL_S)
        self._cache: dict[tuple[str, str], tuple[float, Hashable, SecretValue]] = {}
        self._cache_lock = threading.Lock()

    def reveal(self, ref: SecretRef) -> str:
        """Return prefix + store value. Never stores the str on self."""
        return self._reveal_fetched(ref, self._fetch(ref))

    def reveal_or_none(self, ref: SecretRef) -> str | None:
        """Like :meth:`reveal`, but ``None`` when *ref* is optional and the store has no value.

        A required ref that is missing, and any denied ref, raises as :meth:`reveal` does.
        """
        value = self._fetch_or_none(ref)
        return None if value is None else self._reveal_fetched(ref, value)

    def _fetch_or_none(self, ref: SecretRef) -> SecretValue | None:
        try:
            return self._fetch(ref)
        except SecretMissing:
            if ref.optional:
                return None
            raise

    def _reveal_fetched(self, ref: SecretRef, value: SecretValue) -> str:
        return f"{ref.prefix}{value.reveal()}"

    @property
    def store(self) -> SecretStore:
        """The bound store, so the host can report which kind it chose."""
        return self._store

    def describe(self, ref: SecretRef) -> SecretInfo:
        return self._store.describe(ref)

    def _fetch(self, ref: SecretRef) -> SecretValue:
        """Serve the cache only while the token is the same, not ``None``, and the TTL holds.

        Any other case drops the cached value first, so a failed or deleted secret is never served.
        Every fetch also evicts every entry past its TTL, so a value no one asks for again is not kept.
        """
        key = (ref.name, ref.key)
        now = time.monotonic()
        token = self._store.change_token(ref)
        with self._cache_lock:
            self._evict_expired(now)
            cached = self._cache.pop(key, None)
            if cached is not None and token is not None and cached[1] == token:
                self._cache[key] = cached
                return cached[2]
        value = self._store.get(ref)
        if token is not None and self._ttl_s > 0:
            with self._cache_lock:
                self._cache[key] = (now, token, value)
        return value

    def _evict_expired(self, now: float) -> None:
        for key in [key for key, (ts, _, _) in self._cache.items() if now - ts >= self._ttl_s]:
            del self._cache[key]


class NullResolver(SecretResolver):
    """Default: no store, every reveal is SecretMissing."""

    def __init__(self) -> None:
        super().__init__(_NullStore(), ttl_s=0.0)


class _NullStore:
    def get(self, ref: SecretRef) -> SecretValue:
        raise SecretMissing(ref)

    def describe(self, ref: SecretRef) -> SecretInfo:
        return SecretInfo(present=False, size=0)

    def change_token(self, ref: SecretRef) -> Hashable:
        return None


def current_resolver() -> SecretResolver:
    """The process resolver, else a NullResolver (every reveal is SecretMissing)."""
    return _PROCESS[0] if _PROCESS else NullResolver()


def set_process_resolver(resolver: SecretResolver | None) -> None:
    """Set (or with ``None`` clear) the one resolver of this process."""
    _PROCESS[:] = [resolver] if resolver is not None else []


def reveal_mapping(
    raw: Mapping[str, Any] | None,
    *,
    resolver: SecretResolver | None = None,
) -> dict[str, str]:
    """Reveal SecretRef values in a header or env map. Optional missing refs are omitted.

    A ``None`` or ``""`` plain value is omitted too; any other plain value must be a string.
    """
    if not raw:
        return {}
    active = resolver if resolver is not None else current_resolver()
    fetched: dict[str, tuple[SecretRef, SecretValue] | str] = {}
    for key, value in raw.items():
        name = str(key)
        if isinstance(value, SecretValue):
            raise TypeError(f"{name}: a SecretValue is not a header or env value; pass the SecretRef")
        if is_ref(value):
            ref = parse_secret_ref(value, field=name)
            held = active._fetch_or_none(ref)
            if held is not None:
                fetched[name] = (ref, held)
            continue
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise TypeError(f"{name}: expected a string or a SecretRef, got {type(value).__name__}")
        fetched[name] = value
    # Every required ref is fetched (still opaque) before the first one is revealed.
    return {name: item if isinstance(item, str) else active._reveal_fetched(*item) for name, item in fetched.items()}
