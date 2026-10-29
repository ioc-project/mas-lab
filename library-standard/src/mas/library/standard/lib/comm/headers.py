#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Comm headers: ambient inside one process, explicit on send.

``send`` / ``asend`` take an optional ``headers`` mapping. A link merges it
over the hop-safe ambient headers (:func:`merge_headers`), carries the result
in its envelope, and sets it as the ambient headers while the receiver's
handler runs (:func:`bound_headers`).

Only ``traceparent``, ``tracestate``, and ``baggage`` ride a later send.
``mas.*`` keys and everything else stay on this hop. Names are stored in
lowercase. Callers supply these values; they are unauthenticated.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token

from mas.runtime.boundary.agentcomm.protocol import AgentCommError

MAX_HEADER_COUNT = 32
MAX_HEADER_NAME = 128
MAX_HEADER_VALUE = 4096
MAX_HEADER_TOTAL = 16384
MAX_TRACESTATE_MEMBERS = 32
MAX_BAGGAGE_MEMBERS = 64
HOP_HEADERS = frozenset({"traceparent", "tracestate", "baggage"})
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_LIST_CAPS = {
    "tracestate": MAX_TRACESTATE_MEMBERS,
    "baggage": MAX_BAGGAGE_MEMBERS,
}

_headers: ContextVar[dict[str, str] | None] = ContextVar("mas_comm_headers", default=None)
_log = logging.getLogger(__name__)


def _has_forbidden_char(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or ch in "\u2028\u2029" for ch in value)


def validate_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Reject non-tokens, controls, too many headers, or over-long values.

    Names are lowercased. A later key with the same name wins. An over-long
    ``tracestate`` or ``baggage`` list is dropped so a send still goes through.
    """
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise AgentCommError("headers must be a string-to-string mapping")
    if len(headers) > MAX_HEADER_COUNT:
        raise AgentCommError("too many headers")
    out: dict[str, str] = {}
    total = 0
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise AgentCommError("header names and values must be strings")
        if not key or not _TOKEN.match(key):
            raise AgentCommError(f"header name is not a token: {key!r}")
        if _has_forbidden_char(value):
            raise AgentCommError("headers must not contain control characters")
        name = key.lower()
        if name in _LIST_CAPS:
            members = [part.strip() for part in value.split(",") if part.strip()]
            if len(members) > _LIST_CAPS[name]:
                _log.debug(
                    "dropping %s: %s members exceeds cap %s",
                    name,
                    len(members),
                    _LIST_CAPS[name],
                )
                continue
        value_bytes = value.encode("utf-8")
        if len(name) > MAX_HEADER_NAME or len(value_bytes) > MAX_HEADER_VALUE:
            raise AgentCommError("header too long")
        total += len(name) + len(value_bytes)
        if total > MAX_HEADER_TOTAL:
            raise AgentCommError("headers exceed total size cap")
        out[name] = value
    return out


def _hop_from(headers: Mapping[str, str]) -> dict[str, str]:
    """Allowlisted keys from an already-validated mapping.

    Does not re-validate: only :func:`validate_headers` (or a caller that
    already ran it) may store or pass a mapping here.
    """
    out: dict[str, str] = {}
    for key, value in headers.items():
        name = key.lower()
        if name in HOP_HEADERS:
            out[name] = value
    return out


def hop_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Keys that may ride a later send. ``mas.*`` never hops."""
    return _hop_from(validate_headers(headers))


def get_headers() -> dict[str, str]:
    return dict(_headers.get() or {})


def _set_validated(headers: Mapping[str, str]) -> Token[dict[str, str] | None]:
    """Store an already-validated mapping. Not a public bypass."""
    return _headers.set(dict(headers))


def set_headers(headers: Mapping[str, str]) -> Token[dict[str, str] | None]:
    return _set_validated(validate_headers(headers))


def reset_headers(token: Token[dict[str, str] | None]) -> None:
    _headers.reset(token)


def merge_headers(explicit: Mapping[str, str] | None) -> dict[str, str]:
    """Hop-safe ambient keys, then explicit (explicit wins on a clash)."""
    extra = validate_headers(explicit)
    return {**_hop_from(get_headers()), **extra}


@contextmanager
def _bound_validated(headers: Mapping[str, str]) -> Iterator[None]:
    """Make an already-validated mapping the ambient headers for a block."""
    token = _set_validated(headers)
    try:
        yield
    finally:
        reset_headers(token)


@contextmanager
def bound_headers(headers: Mapping[str, str] | None) -> Iterator[None]:
    """Make ``headers`` the ambient headers for the duration of the block."""
    with _bound_validated(validate_headers(headers)):
        yield
