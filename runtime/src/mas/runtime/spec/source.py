#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve manifest sources to loaded YAML — same path for inline, ref, and id."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from mas.runtime.package_refs import resolve_path_ref

# Re-export: canonical path resolution (file may not exist yet).
resolve_path = resolve_path_ref


def resolve_yaml_path(ref: str, anchor: Path) -> Path:
    """Resolve a ref string to an existing YAML file."""
    path = resolve_path_ref(ref.strip(), anchor)
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {ref!r} -> {path}")
    return path


def resolve_ref_with_search(
    ref: str,
    anchor: Path,
    *,
    search_dirs: list[Path] | None = None,
) -> Path:
    """Resolve ref via :func:`resolve_path_ref`, then optional search directories."""
    try:
        return resolve_yaml_path(ref, anchor)
    except FileNotFoundError:
        pass

    names = [ref.strip()]
    if not ref.endswith(".yaml"):
        names.append(f"{ref.strip()}.yaml")

    for base in search_dirs or []:
        for name in names:
            candidate = (base / name).expanduser()
            if candidate.is_file():
                return candidate.resolve()

    raise FileNotFoundError(f"manifest ref not found: {ref!r} (anchor={anchor})")


@dataclass
class _YamlRecord:
    mtime_ns: int
    size: int
    digest: str
    data: Any


_YAML_CACHE: dict[Path, _YamlRecord] = {}


def _content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached_yaml_document(path: Path) -> Any:
    """Parsed YAML for *path*. Do not mutate. Drops the entry when mtime, size, or hash change."""
    resolved = Path(path).expanduser().resolve()
    st = resolved.stat()
    rec = _YAML_CACHE.get(resolved)
    if rec is not None and rec.mtime_ns == st.st_mtime_ns and rec.size == st.st_size:
        return rec.data
    text = resolved.read_text(encoding="utf-8")
    digest = _content_digest(text)
    if rec is not None and rec.digest == digest:
        rec.mtime_ns = st.st_mtime_ns
        rec.size = st.st_size
        return rec.data
    data = yaml.safe_load(text) or {}
    _YAML_CACHE[resolved] = _YamlRecord(st.st_mtime_ns, st.st_size, digest, data)
    return data


def clear_yaml_file_cache() -> None:
    """Drop memoized YAML parses. Next :func:`load_yaml_file` re-reads from disk."""
    _YAML_CACHE.clear()


def load_yaml_file(path: Path) -> Any:
    return deepcopy(cached_yaml_document(path))


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load YAML file; require top-level mapping."""
    raw = load_yaml_file(path)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: manifest must be a YAML mapping")
    return raw


def load_dataset_items_file(path: Path) -> Any:
    """Load a Dataset YAML file and normalize to ``{"items": [...]}``.

    Supports a Dataset manifest (``apiVersion: lab/v1, kind: Dataset``,
    unwrapped to its ``spec.items``), a plain ``{items: [...]}`` mapping, or
    a bare list of items.

    Shared by callers (e.g. ``library-eval`` providers) that need raw item
    dicts rather than the full ``mas.lab.benchmark.dataset.Dataset`` /
    ``RunInput`` envelope pipeline, so a Dataset-shape change only needs to
    be taught to one loader instead of several hand-rolled copies.
    """
    if path.suffix not in (".yaml", ".yml"):
        raise ValueError(f"Dataset files must be YAML, got {path}")
    data = load_yaml_file(path)
    if isinstance(data, dict) and data.get("kind") == "Dataset":
        spec = data.get("spec") or {}
        return {"items": spec.get("items", [])}
    return data


def resolve_yaml_source(
    *,
    inline: Any = None,
    ref: str | None = None,
    anchor: Path,
    app_ref: dict[str, Any] | None = None,
    sibling: Path | None = None,
    loader: Any | None = None,
) -> Any:
    """Return loaded spec — inline first, then ref, app bundle, sibling file."""
    if inline not in (None, [], {}, ""):
        return inline

    load = loader or load_yaml_file

    if ref and str(ref).strip():
        return load(resolve_yaml_path(str(ref).strip(), anchor))

    if isinstance(app_ref, dict) and app_ref.get("app"):
        return load(resolve_app_resource(app_ref))

    if sibling is not None and sibling.is_file():
        return load(sibling)

    return None


def resolve_manifest_source(
    source: Path | str | dict[str, Any] | None,
    *,
    anchor: Path | None = None,
    loader: Any | None = None,
) -> dict[str, Any] | None:
    """Load any manifest source (inline dict, path, or ref string) into memory."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source

    load = loader or load_yaml_file
    base = anchor or Path.cwd()

    if isinstance(source, Path):
        return load(source)

    if isinstance(source, str):
        stripped = source.strip()
        if not stripped:
            return None
        path = Path(stripped).expanduser()
        if path.is_file():
            return load(path.resolve())
        return load(resolve_yaml_path(stripped, base))

    raise TypeError(f"unsupported manifest source type: {type(source)!r}")


def resolve_app_resource(app_ref: dict[str, Any]) -> Path:
    """Resolve ``{app: name, name: resource.yaml}`` to a file under mas.apps."""
    from mas.apps import get_app

    app_name = app_ref.get("app")
    if not app_name:
        raise ValueError("app_ref missing 'app'")
    resource = app_ref.get("name", "pipeline")
    path = get_app(str(app_name)) / f"{resource}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"app resource not found: {app_ref!r} -> {path}")
    return path
