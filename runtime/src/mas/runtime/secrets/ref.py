#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SecretRef: where a secret lives. Safe to log, dump, and hash."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from mas.runtime.secrets.errors import SecretRefError

NAME_PATTERN = r"\A[a-z0-9][a-z0-9._-]{0,127}\Z"
# An auth scheme word (RFC 9110 token, kept short) and one space, or nothing.
PREFIX_PATTERN = r"\A(?:[A-Za-z][A-Za-z0-9-]{0,31} )?\Z"
_NAME_RE = re.compile(NAME_PATTERN)
_PREFIX_RE = re.compile(PREFIX_PATTERN)

# Protocol header names that must not be a literal string (RFC facts, not a key heuristic).
HTTP_SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
)

# Schema-typed secret fields: their value is a SecretRef, never a literal. Matched without case
# and without ``_`` or ``-`` (see :func:`is_secret_field`), so ``apiKey`` and ``API-KEY`` are ``api_key``.
SECRET_FIELDS = frozenset({"api_key", "password", "token", "secret", "embed_api_key", "client_secret", "private_key"})
_SEPARATORS_RE = re.compile(r"[_-]")


def _fold(key: Any) -> str:
    return _SEPARATORS_RE.sub("", str(key)).lower()


_SECRET_FIELDS_FOLDED = frozenset(_fold(name) for name in SECRET_FIELDS)


def is_secret_field(key: Any) -> bool:
    """True for a key of :data:`SECRET_FIELDS`, compared without case, ``_`` or ``-``."""
    return _fold(key) in _SECRET_FIELDS_FOLDED


def is_header_map_key(key: Any) -> bool:
    """True for a key that holds an HTTP header map: ``headers`` or any key ending in it (``http_headers``)."""
    return _fold(key).endswith("headers")


@dataclass(frozen=True)
class SecretRef:
    """Name and key of a secret. Never a value."""

    name: str
    key: str = "value"
    optional: bool = False
    prefix: str = ""

    def __post_init__(self) -> None:
        name = self.name
        key = "value" if self.key is None or self.key == "" else self.key
        if not name:
            raise SecretRefError("SecretRef needs a name")
        for what, token in (("name", name), ("key", key)):
            if not isinstance(token, str) or not _NAME_RE.match(token):
                raise SecretRefError(
                    f"SecretRef {what} is not a secret name: lowercase letters, digits, '.', '_' "
                    "and '-', starting with a letter or digit, at most 128 characters"
                )
        if not isinstance(self.optional, bool):
            raise SecretRefError(f"secret:{name}: optional must be true or false")
        prefix = self.prefix or ""
        if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
            raise SecretRefError(
                f"secret:{name}: prefix must be one auth scheme word and a space (for example 'Bearer ')"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "key", key)

    def __str__(self) -> str:
        return f"secret:{self.name}/{self.key}"

    def __repr__(self) -> str:
        return str(self)

    def to_manifest(self) -> dict[str, Any]:
        """YAML/JSON form. Short when only the name is set."""
        if self.key == "value" and not self.optional and not self.prefix:
            return {"secret": self.name}
        inner: dict[str, Any] = {"name": self.name, "key": self.key}
        if self.optional:
            inner["optional"] = True
        if self.prefix:
            inner["prefix"] = self.prefix
        return {"secret": inner}


def _is_secret_ref_shape(raw: Any) -> bool:
    """True when *raw* is the mapping ``{secret: ...}`` and nothing else (a SecretRef not yet parsed).

    An Application row may have a ``secret`` field beside ``protocol`` and ``url``.
    That row is not itself a SecretRef.
    """
    return isinstance(raw, Mapping) and set(raw) == {"secret"}


def is_ref(raw: Any) -> bool:
    """True for a SecretRef, parsed (:class:`SecretRef`) or not (:func:`_is_secret_ref_shape`)."""
    return isinstance(raw, SecretRef) or _is_secret_ref_shape(raw)


def parse_secret_ref(raw: Any, *, field: str = "secret") -> SecretRef:
    """Parse short ``{secret: name}`` or long ``{secret: {name, key, optional, prefix}}``."""
    if isinstance(raw, SecretRef):
        return raw
    if not _is_secret_ref_shape(raw):
        raise SecretRefError(
            f"{field} must be a SecretRef object: write {{secret: NAME}} or {{secret: {{name: NAME}}}}"
        )
    inner = raw["secret"]
    if isinstance(inner, str):
        return SecretRef(name=inner)
    if not isinstance(inner, Mapping) or not inner.get("name"):
        raise SecretRefError(f"{field}: secret must be a name or an object with a name")
    unknown = sorted(str(k) for k in inner if k not in {"name", "key", "optional", "prefix"})
    if unknown:
        raise SecretRefError(f"{field}: SecretRef has unknown keys {unknown}")
    return SecretRef(
        name=inner["name"],
        key=inner.get("key") or "value",
        optional=inner.get("optional", False),
        prefix=inner.get("prefix") or "",
    )


def parse_secret_ref_optional(raw: Any, *, field: str = "secret") -> SecretRef | None:
    if raw is None or raw == "":
        return None
    return parse_secret_ref(raw, field=field)


def iter_secret_refs(obj: Any) -> Iterator[SecretRef]:
    """Walk a loaded manifest and yield every SecretRef (hydrated or still a mapping)."""
    if isinstance(obj, SecretRef):
        yield obj
        return
    if _is_secret_ref_shape(obj):
        yield parse_secret_ref(obj)
        return
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from iter_secret_refs(value)
        return
    if isinstance(obj, list):
        for item in obj:
            yield from iter_secret_refs(item)


def removed_key_problem(key: Any, value: Any) -> str | None:
    """What is wrong with the mapping entry ``key: value`` when it uses a removed form (a ``*_env`` key,
    an ``env:`` string), else ``None``. The text names the key, never the value."""
    if str(key).endswith("_env"):
        return f"{key} is removed; set a SecretRef instead ({{secret: {{name: NAME, key: value}}}})"
    if isinstance(value, str) and value.startswith("env:"):
        return (
            "env: prefix is removed; write a SecretRef "
            "({secret: NAME}) for a secret, or a plain overlay value for a host."
        )
    return None


def header_problem(name: Any, value: Any) -> str | None:
    """What is wrong with header ``name: value`` when a listed HTTP auth header is not a SecretRef, else ``None``."""
    if str(name).strip().lower() not in HTTP_SECRET_HEADERS or is_ref(value):
        return None
    return (
        f"this header must be a SecretRef, not a {type(value).__name__}; write {{secret: {{name: NAME, key: value}}}}."
    )


def check_http_header_secrets(headers: Mapping[str, Any] | None, *, path: str = "headers") -> None:
    """A listed HTTP auth header holds a SecretRef and nothing else."""
    for name, value in (headers or {}).items():
        problem = header_problem(name, value)
        if problem is not None:
            raise SecretRefError(f"{path}.{name}: {problem}")
