#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load lab JSON Schema documents with cross-file $id refs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mas.lab.schemas.paths import lab_schema_dir
from mas.runtime.spec.source import load_yaml_file

_SCHEMA_SUFFIXES = {".yaml", ".yml", ".json"}


def _load_schema_file(path: Path) -> Any:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return load_yaml_file(path)


def _schema_dir_stamp(root: Path) -> tuple[tuple[str, int, int], ...]:
    stamps: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _SCHEMA_SUFFIXES:
            continue
        st = path.stat()
        stamps.append((str(path.relative_to(root)), st.st_mtime_ns, st.st_size))
    return tuple(stamps)


_REGISTRY_CACHE: dict[Path, tuple[tuple[tuple[str, int, int], ...], Any]] = {}


def schema_registry_from_dir(root: Path):
    """Registry of every schema ``$id`` under *root*.

    Rebuilt when any schema file's mtime or size changes.
    """
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    resolved = Path(root).resolve()
    stamp = _schema_dir_stamp(resolved)
    hit = _REGISTRY_CACHE.get(resolved)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    resources: list[tuple[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        if not path.is_file() or path.suffix not in _SCHEMA_SUFFIXES:
            continue
        data = _load_schema_file(path)
        if not isinstance(data, dict):
            continue
        uri = data.get("$id")
        if not isinstance(uri, str) or not uri:
            continue
        resource = Resource.from_contents(data, default_specification=DRAFT7)
        resources.append((uri, resource))
    registry = Registry().with_resources(resources).crawl()
    _REGISTRY_CACHE[resolved] = (stamp, registry)
    return registry


def lab_schema_registry():
    """Registry of every ``$id`` under ``docs/schemas/lab``."""
    return schema_registry_from_dir(lab_schema_dir())


def load_lab_schema(name: str) -> dict[str, Any]:
    """Load ``<name>.schema.yaml`` from the lab schema directory."""
    path: Path = lab_schema_dir() / f"{name}.schema.yaml"
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise TypeError(f"schema {path} is not a mapping")
    return data


def validate_against_lab_schema(name: str, instance: Any) -> None:
    import jsonschema

    schema = load_lab_schema(name)
    jsonschema.Draft7Validator(schema, registry=lab_schema_registry()).validate(instance)
