#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One alias table for middleware call kinds and leftover YAML keys.

Plugins declare ``call_kinds`` / ``per``. Manifests write ``for`` and ``per``.
``applies_to``, ``pipeline``, ``interceptors``, and ``infra_interceptors``
are read-only aliases and warn once per (old, new, where). Mixing an alias
with its new name in one mapping is an error.
"""

from __future__ import annotations

import warnings
from typing import Any

_CALL_KIND_ALIASES: dict[str, str] = {
    "llm": "llm",
    "llm_call": "llm",
    "LLM_CALL": "llm",
    "tool": "tool",
    "tool_http": "tool",
    "TOOL_HTTP": "tool",
}

_SEEN_ALIAS_WARNINGS: set[tuple[str, str, str]] = set()


def normalize_call_kind(raw: Any) -> str:
    """Return ``llm``, ``tool``, or a lowercase leftover token."""
    token = str(raw or "").strip()
    if not token:
        return ""
    mapped = _CALL_KIND_ALIASES.get(token) or _CALL_KIND_ALIASES.get(token.lower())
    if mapped:
        return mapped
    return token.lower().replace("-", "_")


def op_kind(op: str) -> str:
    """Map an engine ``op`` (``LLM_CALL``) to a ``for`` token (``llm``)."""
    token = str(op or "").strip()
    if token == "LLM_CALL":
        return "llm"
    if token == "TOOL_CALL":
        return "tool"
    return normalize_call_kind(token)


def call_kind_matches(op: str, kinds: Any) -> bool:
    """True when *op* is in *kinds*. Empty *kinds* matches nothing.

    Undeclared steps get ``llm`` from :func:`middleware_call_kinds`, not here.
    """
    declared = normalize_call_kinds(kinds)
    if not declared:
        return False
    return op_kind(op) in declared


def normalize_call_kinds(raw: Any) -> tuple[str, ...]:
    """Canonical call kinds from a string, list, or missing value.

    An empty result means the plugin did not declare a kind. Callers
    treat that as ``llm`` (the pre-``for`` default).
    """
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [raw]
    out: list[str] = []
    for item in items:
        kind = normalize_call_kind(item)
        if kind and kind not in out:
            out.append(kind)
    return tuple(out)


def warn_legacy_key(old: str, new: str, *, where: str) -> None:
    key = (old, new, where)
    if key in _SEEN_ALIAS_WARNINGS:
        return
    _SEEN_ALIAS_WARNINGS.add(key)
    warnings.warn(
        f"{old} is a read-only alias of {new} ({where}); write {new}",
        DeprecationWarning,
        stacklevel=3,
    )


def read_aliased_field(
    mapping: dict[str, Any],
    canonical: str,
    alias: str,
    *,
    where: str,
) -> Any:
    """Return *canonical*, or *alias* with a warning. Mixing both is an error."""
    has_new = canonical in mapping
    has_old = alias in mapping
    if has_new and has_old:
        raise ValueError(f"{where}: mix of {canonical!r} and {alias!r} is an error")
    if has_new:
        return mapping[canonical]
    if has_old:
        warn_legacy_key(alias, canonical, where=where)
        return mapping[alias]
    return None


def as_str_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item).strip()]
    return [str(raw)]


def reset_alias_warnings() -> None:
    """Test helper: allow the same alias warning again."""
    _SEEN_ALIAS_WARNINGS.clear()
