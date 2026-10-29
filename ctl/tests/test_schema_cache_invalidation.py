#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolved schema cache: per-call stamps, content hash, clears, and threads."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import pytest
import yaml
from mas.ctl.validate.schemas import (
    _RESOLVED_SCHEMA,
    _load_resolved_schema_file,
    clear_schema_caches,
)


def _write_yaml(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def _stamp_paths(path: Path) -> set[str]:
    stamps, _doc = _RESOLVED_SCHEMA[path.resolve()]
    return {loc for loc, *_rest in stamps}


def test_clear_schema_caches_empties_resolved_and_yaml_caches(tmp_path: Path) -> None:
    from mas.runtime.spec.source import _YAML_CACHE

    root = tmp_path / "root.yaml"
    _write_yaml(root, {"type": "string"})
    _load_resolved_schema_file(root)
    assert _RESOLVED_SCHEMA
    assert _YAML_CACHE
    clear_schema_caches()
    assert not _RESOLVED_SCHEMA
    assert not _YAML_CACHE


def test_resolved_schema_keeps_the_cache_when_only_mtime_changes(tmp_path: Path) -> None:
    root = tmp_path / "root.yaml"
    _write_yaml(root, {"type": "string"})
    clear_schema_caches()
    first = _load_resolved_schema_file(root)
    os.utime(root, (root.stat().st_atime + 5, root.stat().st_mtime + 5))
    second = _load_resolved_schema_file(root)
    assert first is second


def _rewrite_same_size_holding_mtime(path: Path, text: str) -> None:
    st = path.stat()
    payload = text.encode("utf-8")
    if len(payload) != st.st_size:
        raise AssertionError(f"rewrite size {len(payload)} != {st.st_size}")
    path.write_bytes(payload)
    os.utime(path, ns=(path.stat().st_atime_ns, st.st_mtime_ns))
    assert path.stat().st_mtime_ns == st.st_mtime_ns
    assert path.stat().st_size == st.st_size


def test_resolved_schema_sees_a_size_change_with_mtime_held(tmp_path: Path) -> None:
    shared = tmp_path / "shared.yaml"
    root = tmp_path / "root.yaml"
    _write_yaml(shared, {"type": "string"})
    _write_yaml(root, {"properties": {"x": {"$ref": "./shared.yaml"}}})
    clear_schema_caches()
    first = _load_resolved_schema_file(root)
    assert first["properties"]["x"] == {"type": "string"}
    mtime_ns = shared.stat().st_mtime_ns
    _write_yaml(shared, {"type": "boolean"})
    os.utime(shared, ns=(shared.stat().st_atime_ns, mtime_ns))
    assert shared.stat().st_mtime_ns == mtime_ns
    second = _load_resolved_schema_file(root)
    assert second["properties"]["x"] == {"type": "boolean"}
    assert first is not second


def test_resolved_schema_sees_a_same_size_rewrite_with_mtime_held(tmp_path: Path) -> None:
    shared = tmp_path / "shared.yaml"
    root = tmp_path / "root.yaml"
    shared.write_text("type: string\n", encoding="utf-8")
    _write_yaml(root, {"properties": {"x": {"$ref": "./shared.yaml"}}})
    clear_schema_caches()
    first = _load_resolved_schema_file(root)
    assert first["properties"]["x"] == {"type": "string"}
    _rewrite_same_size_holding_mtime(shared, "type: number\n")
    second = _load_resolved_schema_file(root)
    assert second["properties"]["x"] == {"type": "number"}
    assert first is not second


def test_resolved_schema_raises_when_a_cached_fragment_is_removed(tmp_path: Path) -> None:
    shared = tmp_path / "shared.yaml"
    root = tmp_path / "root.yaml"
    _write_yaml(shared, {"type": "string"})
    _write_yaml(root, {"properties": {"x": {"$ref": "./shared.yaml"}}})
    clear_schema_caches()
    _load_resolved_schema_file(root)
    shared.unlink()
    with pytest.raises(FileNotFoundError):
        _load_resolved_schema_file(root)


def test_cache_hit_adds_fragment_stamps_to_the_parent(tmp_path: Path) -> None:
    shared = tmp_path / "shared.yaml"
    root_a = tmp_path / "a.yaml"
    root_b = tmp_path / "b.yaml"
    _write_yaml(shared, {"type": "string"})
    _write_yaml(root_a, {"properties": {"x": {"$ref": "./shared.yaml"}}})
    _write_yaml(root_b, {"properties": {"y": {"$ref": "./shared.yaml"}}})
    clear_schema_caches()
    _load_resolved_schema_file(root_a)
    _load_resolved_schema_file(root_b)
    assert str(shared.resolve()) in _stamp_paths(root_b)
    _write_yaml(shared, {"type": "boolean"})
    second = _load_resolved_schema_file(root_b)
    assert second["properties"]["y"] == {"type": "boolean"}


@pytest.mark.timeout(2)
def test_concurrent_schema_loads_keep_own_dependency_stamps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mas.runtime.spec.source import cached_yaml_document

    shared = tmp_path / "shared.yaml"
    isolated = tmp_path / "isolated.yaml"
    _write_yaml(shared, {"type": "string"})
    _write_yaml(isolated, {"type": "integer"})
    roots = []
    for name in ("a", "b", "c", "d", "e"):
        root = tmp_path / f"{name}.yaml"
        _write_yaml(root, {"properties": {"x": {"$ref": "./shared.yaml"}}})
        roots.append(root)
    isolated_root = tmp_path / "iso.yaml"
    _write_yaml(isolated_root, {"$ref": "./isolated.yaml"})

    clear_schema_caches()
    real = cached_yaml_document

    def delayed(path: Path) -> object:
        time.sleep(0.01)
        return real(path)

    monkeypatch.setattr("mas.ctl.validate.schemas.cached_yaml_document", delayed)
    paths = [*roots, isolated_root]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_load_resolved_schema_file, path) for path in paths * 2]
        done, not_done = wait(futs, timeout=1.5)
        for fut in not_done:
            fut.cancel()
        assert not not_done
        for fut in done:
            fut.result()

    assert str(isolated.resolve()) in _stamp_paths(isolated_root)
    assert str(isolated.resolve()) not in _stamp_paths(roots[0])
    for root in roots:
        stamps = _stamp_paths(root)
        assert str(root.resolve()) in stamps
        assert str(shared.resolve()) in stamps
        assert str(isolated.resolve()) not in stamps
    for path, (stamps, _doc) in _RESOLVED_SCHEMA.items():
        stamp_paths = {loc for loc, *_rest in stamps}
        assert str(path.resolve()) in stamp_paths
