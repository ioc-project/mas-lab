#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Small fakes for identity tests: an in-session channel, a request builder and a bound secret."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Hashable, cast

from mas.library.standard.lib.identity.contract import Audience, PresentRequest
from mas.runtime.secrets import (
    NullResolver,
    SecretRef,
    SecretResolver,
    SecretValue,
    current_resolver,
    set_process_resolver,
)
from mas.runtime.secrets.errors import SecretMissing
from mas.runtime.secrets.store import SecretInfo

#: A value no real credential contains. Configure a scheme with it, then scan every public sink.
CANARY = "canary-7d41f2e0-not-a-real-secret"

#: The clock value tests use by default (``make_request``).
FAKE_NOW = 1_000_000.0


class FakeChannel:
    """A :class:`~mas.library.standard.lib.identity.contract.Channel` that records requests and answers from a queue."""

    def __init__(self, replies: Sequence[Mapping[str, Any] | Exception] = ()) -> None:
        self._replies = list(replies)
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.data_keys: list[str | None] = []

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        self.requests.append((method, dict(params)))
        self.data_keys.append(data_key)
        if not self._replies:
            return {}
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class _MemoryStore:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = values if isinstance(values, dict) else dict(values)  # a dict is shared: later entries show

    def get(self, ref: SecretRef) -> SecretValue:
        if ref.name not in self._values:
            raise SecretMissing(ref)
        return SecretValue(self._values[ref.name], name=ref.name)

    def describe(self, ref: SecretRef) -> SecretInfo:
        value = self._values.get(ref.name)
        return SecretInfo(present=value is not None, size=len(value or ""))

    def change_token(self, ref: SecretRef) -> Hashable:
        return None


@contextmanager
def bound_secrets(values: Mapping[str, str]) -> Iterator[None]:
    """For the block, ``{secret: NAME}`` refs resolve to ``values[NAME]``; the previous resolver is restored after."""
    previous = current_resolver()
    set_process_resolver(SecretResolver(_MemoryStore(values), ttl_s=0.0))
    try:
        yield
    finally:
        set_process_resolver(None if isinstance(previous, NullResolver) else previous)


def make_request(
    record: str = "rec1",
    *,
    protocol: str = "mcp",
    target: str = "https://tools.example",
    task: str = "tools/call:search",
    actor: str = "agent-a",
    subject: str = "user-1",
    now: float = FAKE_NOW,
    **extra: Any,
) -> PresentRequest:
    """A well-formed request. ``extra`` keys (identity, phase, peer, request, ...) are added as given."""
    req: dict[str, Any] = {
        "record": record,
        "principal": {"actor": actor, "subject": subject},
        "audience": Audience(protocol=protocol, target=target),
        "task": task,
        "now": now,
    }
    req.update(extra)
    return cast(PresentRequest, req)
