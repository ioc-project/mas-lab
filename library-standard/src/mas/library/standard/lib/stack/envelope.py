#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The protocol-neutral message a layer receives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class Envelope:
    """What travels down a stack.

    ``payload`` is whatever the top adapter hands over (a task string, a tool
    call, a chat request). ``headers`` are wire headers a layer may add or drop.
    ``meta`` is routing and bookkeeping that does not go on the wire as is:
    ``target``, ``sender``, ``intent``, ``correlation_id`` and so on. A layer
    never edits an envelope; it builds a new one with ``with_``.
    """

    payload: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))

    def with_(
        self,
        *,
        payload: Any = ...,
        headers: Mapping[str, str] | None = None,
        meta: Mapping[str, Any] | None = None,
        drop_headers: Iterable[str] = (),
    ) -> Envelope:
        """Copy with ``payload`` replaced, ``headers`` and ``meta`` merged over the old ones,
        and every header named in ``drop_headers`` removed.

        A key in ``headers`` replaces the old value of that key; the other old
        keys stay. ``drop_headers`` runs after the merge, so a key that is both
        set and dropped is gone. Header names compare without case. ``meta``
        keys are only added or replaced, never removed.
        """
        if isinstance(drop_headers, (str, bytes)):
            raise TypeError("drop_headers is a list of header names, not one string")
        changes: dict[str, Any] = {}
        if payload is not ...:
            changes["payload"] = payload
        merged = {**self.headers, **headers} if headers else None
        drop = {str(name).lower() for name in drop_headers}
        if drop:
            base = merged if merged is not None else dict(self.headers)
            merged = {key: value for key, value in base.items() if key.lower() not in drop}
        if merged is not None:
            changes["headers"] = merged
        if meta:
            changes["meta"] = {**self.meta, **meta}
        return replace(self, **changes)
