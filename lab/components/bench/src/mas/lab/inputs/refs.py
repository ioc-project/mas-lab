#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""File references inside dataset items: ``{ref: path[#id]}`` and path shorthands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas.runtime.spec.source import load_yaml_file

_REF_KEYS = frozenset({"ref", "id"})


def is_ref_object(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and set(value.keys()) <= _REF_KEYS


def resolve_ref_object(value: dict, base_path: Optional[Path]) -> Any:
    if "id" in value and "ref" not in value:
        raise ValueError(f"catalog id refs are not resolved yet: {value.get('id')!r}")
    ref = value.get("ref")
    if not isinstance(ref, str):
        raise TypeError("ref object needs ref: path")
    return load_ref(ref, base_path)


def resolve_file_slot(value: Any, base_path: Optional[Path]) -> Any:
    """tool_fixtures / memory_seeds: ``{ref:}`` or bare path shorthand."""
    if value is None:
        return None
    if isinstance(value, str):
        return load_ref(value, base_path)
    if is_ref_object(value):
        return resolve_ref_object(value, base_path)
    return value


def resolve_text_slot(value: Any, base_path: Optional[Path]) -> Any:
    """user / hitl / expectations: a string is inline text; only ``{ref:}`` loads a file."""
    if value is None:
        return None
    if is_ref_object(value):
        return resolve_ref_object(value, base_path)
    return value


def _split_ref(text: str) -> tuple[str, str | None]:
    path, sep, frag = str(text).partition("#")
    if not sep:
        return str(text), None
    return path, (frag.strip() or None)


def _pick_fragment(data: Any, frag: str) -> Any:
    """Select ``#id`` from a list of ``{id: ...}`` or a mapping keyed by id.

    Tries each candidate nested-list key in turn: a miss on one candidate
    (``ValueError``) falls through to the next candidate.
    """
    if isinstance(data, dict):
        if frag in data:
            return data[frag]
        if str(data.get("id")) == frag:
            return data
        for key in ("items", "fixtures", "entries", "messages", "turns", "user"):
            nested = data.get(key)
            if isinstance(nested, list):
                try:
                    return _pick_fragment(nested, frag)
                except ValueError:
                    continue
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("id")) == frag:
                return item
    raise ValueError(f"fixture id {frag!r} not found")


def resolve_ref_path(ref: str, base_path: Optional[Path]) -> Path:
    """Resolve ``ref`` to an existing file. Does not read the file."""
    path_text, _frag = _split_ref(ref)
    path = Path(path_text)
    if base_path and not path.is_absolute():
        path = base_path / path
    if not path.is_file() and ":" in path_text:
        from mas.runtime.package_refs import resolve_path_ref

        resolved = resolve_path_ref(path_text, base_path or Path("."))
        if resolved.is_file():
            path = resolved
    if not path.is_file():
        raise FileNotFoundError(f"dataset ref {ref!r} not found (looked for {path})")
    return path


def load_binary_ref(ref: str, base_path: Optional[Path]) -> bytes:
    """Read a part ref as bytes (images, PDFs). Never YAML-decode."""
    return resolve_ref_path(ref, base_path).read_bytes()


def load_ref(ref: str, base_path: Optional[Path]) -> Any:
    path = resolve_ref_path(ref, base_path)
    _path_text, frag = _split_ref(ref)
    if path.suffix.lower() in {".txt", ".md"}:
        if frag:
            raise ValueError(f"text ref {ref!r} does not support #fragments")
        return path.read_text(encoding="utf-8").rstrip("\n")
    data = load_yaml_file(path)
    if frag:
        data = _pick_fragment(data, frag)
    return data
