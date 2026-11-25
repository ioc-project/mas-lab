#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Who is acting: a context-local principal with explicit propagation across threads and loops.

The principal is ids and opaque references only (``actor``, ``subject``, ``session`` and the delegation
``chain``), never a secret. Inside one task and its child tasks a ``ContextVar`` carries it. A **worker thread
starts with an empty context**, so nothing here relies on inheritance: wrap the principal where it is known in a
:class:`CapturedPrincipal`, hand it across, and re-bind it on the other side.

    captured = CapturedPrincipal(current_principal())   # on the calling side
    ...
    with captured.bind():                                # in the worker thread or the other loop
        current_principal()
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mas.library.standard.lib.identity.contract import MAX_CHAIN, Principal

PRINCIPAL_KEYS: tuple[str, ...] = ("actor", "subject", "session")
MAX_FIELD = 256

_PRINCIPAL: ContextVar[Principal | None] = ContextVar("mas_identity_principal", default=None)


def normalize_principal(principal: Mapping[str, Any] | None) -> Principal:
    """A clean copy: known keys only, string values, each at most :data:`MAX_FIELD` characters.

    Unknown keys and non-string or over-long values raise ``ValueError`` (a principal is not a bag).
    Empty values are dropped. ``chain`` is a list of at most :data:`~mas.library.identity.contract.MAX_CHAIN` short
    string ids (nearest actor first).
    """
    out: Principal = {}
    for key, value in (principal or {}).items():
        if key == "chain":
            if not isinstance(value, (list, tuple)) or len(value) > MAX_CHAIN:
                raise ValueError("a chain is a short list of ids")
            if any(not isinstance(item, str) or not item or len(item) > MAX_FIELD for item in value):
                raise ValueError("a chain is a short list of ids")
            if value:
                out["chain"] = list(value)
            continue
        if key not in PRINCIPAL_KEYS:
            raise ValueError("unknown principal field")
        if not isinstance(value, str) or len(value) > MAX_FIELD:
            raise ValueError("principal fields are short strings")
        if value:
            out[key] = value  # type: ignore[literal-required]
    return out


def current_principal() -> Principal:
    """The principal bound in this context, as a copy. Empty when none is bound."""
    bound = _PRINCIPAL.get()
    return Principal(**bound) if bound else Principal()


@contextmanager
def bind(principal: Mapping[str, Any] | None) -> Iterator[Principal]:
    """Make ``principal`` the acting principal for the block (it replaces, never merges)."""
    clean = normalize_principal(principal)
    token = _PRINCIPAL.set(clean)
    try:
        yield Principal(**clean)
    finally:
        _PRINCIPAL.reset(token)


@dataclass(frozen=True)
class CapturedPrincipal:
    """A principal taken in one place, re-bindable in another thread or loop."""

    principal: Principal

    @contextmanager
    def bind(self) -> Iterator[Principal]:
        """Bind the captured principal for the block."""
        with bind(self.principal) as bound:
            yield bound
