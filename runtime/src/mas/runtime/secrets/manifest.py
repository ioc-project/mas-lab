#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest checks before load: no removed keys, no literal secret, no credential in a URL or header.

Every slot rule lives in :func:`iter_manifest_problems`; a loader raises its first problem, hardening lists them all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.ref import (
    SecretRef,
    _is_secret_ref_shape,
    header_problem,
    is_header_map_key,
    is_ref,
    is_secret_field,
    parse_secret_ref,
    removed_key_problem,
)
from mas.runtime.secrets.url import check_url_has_no_credentials
from mas.runtime.spec.source import load_yaml_file

# Counted with every alias expanded: a document past either limit is refused.
MAX_MANIFEST_NODES = 100_000
MAX_MANIFEST_DEPTH = 100

_CONTAINERS = (Mapping, list, tuple, set, frozenset)
_TOO_LARGE = "manifest too large or too deeply nested"
# A string that names where a secret used to be read from, not a secret: ``env:`` is refused
# anywhere (a removed form), ``file:`` in a secret slot.
_REFERENCE_PREFIXES = ("env:", "file:")


class ManifestProblem(NamedTuple):
    """One thing wrong with a manifest: where (a dotted path, ``""`` for the document) and what.

    ``str()`` is the sentence :func:`prepare_manifest` raises. Neither part holds a value of the document.
    """

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def iter_manifest_problems(data: Any, *, source: str = "") -> Iterator[ManifestProblem]:
    """Every slot rule broken by *data*, as ``(path, message)`` pairs, in walk order.

    The rules: a removed ``*_env`` key or an ``env:`` string anywhere; a secret field (:data:`SECRET_FIELDS`),
    a listed HTTP auth header or an ``admission.callers`` entry that is not a SecretRef (a literal, or a
    ``file:`` reference); a header map that is not a mapping; a malformed ``{secret: ...}``; a credential
    in a URL (a value, a list item or a key). A document past :data:`MAX_MANIFEST_NODES` or
    :data:`MAX_MANIFEST_DEPTH` yields that one problem. *source* (a file name), when given, leads each path.
    """
    for path, message in _check(data)[1]:
        if source:
            path = f"{source}: {path}" if path else source
        yield ManifestProblem(path, message)


def prepare_manifest(data: Any, *, source: str = "") -> Any:
    """Return *data* with every ``{secret: ...}`` mapping hydrated to a SecretRef, or raise the first
    problem of :func:`iter_manifest_problems` as ``SecretRefError`` (*source* leads the message).
    Every loader of a document that may hold a secret (infra, services, environments, flavours) calls it."""
    value, problems = _check(data)
    if problems:
        text = str(problems[0])
        raise SecretRefError(f"{source}: {text}" if source else text)
    return value


def check_manifest(data: Any, *, source: str = "") -> None:
    """The checks of :func:`prepare_manifest` alone: *data* is left as it is, ``{secret: ...}`` included
    (for a loader that keeps the document as data and hands SecretRef mappings on unchanged)."""
    prepare_manifest(data, source=source)


def load_checked_document(source: str | Path | Mapping[str, Any], *, name: str = "") -> dict[str, Any]:
    """A document from a path (JSON for ``.json``, else YAML) or a mapping, refused as :func:`check_manifest` does.

    SecretRefs stay data (``{secret: ...}`` mappings). *name* leads an error (default: the path, or
    ``document`` for a mapping). An error never quotes the text; an empty file is an empty mapping.
    Anything but a mapping is refused.
    """
    if isinstance(source, Mapping):
        doc: Any = source
        where = name or "document"
    else:
        path = Path(source)
        where = name or str(path)
        if path.suffix != ".json":
            doc = load_yaml_file(path)
        else:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{where}: not valid JSON (line {exc.lineno}, column {exc.colno})") from None
    if not isinstance(doc, Mapping):
        raise ValueError(f"{where}: must be a mapping")
    check_manifest(doc, source=where)
    return doc if isinstance(doc, dict) else dict(doc)


def is_secret_literal(value: Any) -> bool:
    """True when *value*, under a secret field, would hold the secret itself rather than a SecretRef.

    A non-empty string, a number, bytes, or a non-empty list, tuple, set or mapping that is not a
    SecretRef. ``None``, a boolean and an empty value hold nothing.
    """
    if isinstance(value, (bool, SecretRef)) or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, _CONTAINERS):
        return bool(value)
    return isinstance(value, (int, float, bytes))


def _slot_literal_message(raw: Any, what: str) -> str:
    """The refusal of a secret slot holding *raw* (a literal or a ``file:`` / ``env:`` reference)."""
    if isinstance(raw, str) and raw.startswith(_REFERENCE_PREFIXES):
        return f"{what} must be a SecretRef, not a {raw.split(':', 1)[0]}: reference; write {{secret: NAME}}"
    return f"{what} must be a SecretRef; write {{secret: NAME}}"


def _url(text: Any, field: str, problems: list[ManifestProblem]) -> None:
    try:
        check_url_has_no_credentials(text, field=field)
    except SecretRefError as exc:
        problems.append(ManifestProblem(field, str(exc).removeprefix(f"{field}: ")))


def _entry(key: Any, raw: Any, value: Any, path: str, parent_key: Any, problems: list[ManifestProblem]) -> None:
    """The rules of one mapping entry (*raw* as loaded, *value* hydrated), header map and
    ``admission.callers`` entries included."""
    here = f"{path}.{key}" if path else str(key)
    removed = removed_key_problem(key, raw if isinstance(raw, str) else None)
    if removed is not None:
        problems.append(ManifestProblem(here, removed))
    if is_header_map_key(key):
        if isinstance(raw, Mapping):
            for name, item in raw.items():
                problem = header_problem(name, item)
                if problem is not None:
                    problems.append(ManifestProblem(f"{here}.{name}", problem))
        elif raw:
            problems.append(ManifestProblem(here, "headers must be a mapping of header name to value"))
    if is_secret_field(key) and removed is None and not is_ref(raw) and is_secret_literal(raw):
        problems.append(ManifestProblem(here, _slot_literal_message(raw, "a secret field")))
    if key == "callers" and parent_key == "admission":
        if isinstance(raw, Mapping):  # an ``env:`` value is a removed key already
            items = [(f"{here}.{n}", v) for n, v in raw.items() if not (isinstance(v, str) and v.startswith("env:"))]
        else:
            items = [(f"{here}[{i}]", v) for i, v in enumerate(raw)] if isinstance(raw, (list, tuple)) else []
        for where, item in items:
            if not is_ref(item) and is_secret_literal(item):
                problems.append(ManifestProblem(where, _slot_literal_message(item, "a caller token")))
    if isinstance(key, str):
        _url(key, f"{path or 'value'} (a key)", problems)
    if isinstance(value, str):
        _url(value, here, problems)


class _TooLarge(Exception):
    pass


def _check(data: Any) -> tuple[Any, list[ManifestProblem]]:
    """*data* hydrated and its problems; a document past a limit is only that problem."""
    problems: list[ManifestProblem] = []
    if isinstance(data, str):
        _url(data, "value", problems)
    try:
        return _walk(data, "", None, 0, problems, [0]), problems
    except _TooLarge:
        return None, [ManifestProblem("", _TOO_LARGE)]


def _walk(raw: Any, path: str, slot: Any, depth: int, problems: list[ManifestProblem], count: list[int]) -> Any:
    """*raw* with its ``{secret: ...}`` mappings hydrated; problems below it appended to *problems*.

    *slot* is the key of *raw* in its parent mapping. Recursive: an alias is walked at each place it
    appears and counts at its expanded size; a cycle hits the depth cap."""
    if _is_secret_ref_shape(raw):
        try:
            return parse_secret_ref(raw)
        except SecretRefError as exc:
            problems.append(ManifestProblem(path, str(exc)))
            return raw
    if not isinstance(raw, _CONTAINERS):
        return raw
    count[0] += len(raw)
    if depth >= MAX_MANIFEST_DEPTH or count[0] > MAX_MANIFEST_NODES:
        raise _TooLarge
    if isinstance(raw, Mapping):
        built = {}
        for key, item in raw.items():
            here = f"{path}.{key}" if path else str(key)
            built[key] = value = _walk(item, here, key, depth + 1, problems, count)
            _entry(key, item, value, path, slot, problems)
        return built
    values = []
    for index, item in enumerate(raw):
        values.append(_walk(item, f"{path}[{index}]", None, depth + 1, problems, count))
        if isinstance(values[-1], str):
            _url(values[-1], f"{path}[{index}]", problems)
    return values if isinstance(raw, list) else type(raw)(values)
