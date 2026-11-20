#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Batch-scoped YAML cache: counts, copies, invalidation, drop at end."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from mas.library_roots import _installed_named_libraries, clear_library_discovery_cache
from mas.runtime.spec.batch_cache import (
    BatchCache,
    bind_batch_cache,
    current_batch_cache,
    reset_batch_cache,
)
from mas.runtime.spec.source import load_yaml_file, load_yaml_mapping


def test_second_load_does_not_call_yaml_safe_load(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\nmetadata: {name: a}\n", encoding="utf-8")
    cache = BatchCache()
    token = bind_batch_cache(cache)
    parsed = {"n": 0}
    real = yaml.safe_load

    def wrapped(text: object, *args: object, **kwargs: object) -> object:
        parsed["n"] += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr("mas.runtime.spec.batch_cache.yaml.safe_load", wrapped)
    try:
        first = load_yaml_mapping(path)
        second = load_yaml_mapping(path)
        assert first == second == {"kind": "Agent", "metadata": {"name": "a"}}
        assert parsed["n"] == 1
    finally:
        reset_batch_cache(token)


def test_mutated_tree_does_not_change_the_cached_copy(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\nspec: {id: a}\n", encoding="utf-8")
    cache = BatchCache()
    token = bind_batch_cache(cache)
    try:
        first = load_yaml_file(path)
        first["spec"]["id"] = "mutated"
        first["extra"] = True
        second = load_yaml_file(path)
        assert second == {"kind": "Agent", "spec": {"id": "a"}}
        assert "extra" not in second
    finally:
        reset_batch_cache(token)


def test_changed_file_is_reread(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\n", encoding="utf-8")
    cache = BatchCache()
    token = bind_batch_cache(cache)
    parsed = {"n": 0}
    real = yaml.safe_load

    def wrapped(text: object, *args: object, **kwargs: object) -> object:
        parsed["n"] += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr("mas.runtime.spec.batch_cache.yaml.safe_load", wrapped)
    try:
        assert load_yaml_file(path)["kind"] == "Agent"
        path.write_text("kind: MAS\n", encoding="utf-8")
        assert load_yaml_file(path)["kind"] == "MAS"
        assert parsed["n"] == 2
    finally:
        reset_batch_cache(token)


def test_cache_dropped_at_clear_reparses(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\n", encoding="utf-8")
    cache = BatchCache()
    token = bind_batch_cache(cache)
    parsed = {"n": 0}
    real = yaml.safe_load

    def wrapped(text: object, *args: object, **kwargs: object) -> object:
        parsed["n"] += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr("mas.runtime.spec.batch_cache.yaml.safe_load", wrapped)
    try:
        load_yaml_file(path)
        cache.clear()
        load_yaml_file(path)
        assert parsed["n"] == 2
        assert current_batch_cache() is cache
    finally:
        reset_batch_cache(token)
        assert current_batch_cache() is None


def test_installed_libraries_call_entry_points_once_per_batch(monkeypatch) -> None:
    import importlib.metadata

    calls = {"n": 0}
    real = importlib.metadata.entry_points

    def wrapped(*args: object, **kwargs: object):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "entry_points", wrapped)
    clear_library_discovery_cache()
    _installed_named_libraries()
    _installed_named_libraries()
    first = calls["n"]
    assert first >= 1
    _installed_named_libraries()
    assert calls["n"] == first
    clear_library_discovery_cache()
    _installed_named_libraries()
    assert calls["n"] == first + 1
    clear_library_discovery_cache()


def test_a_hit_returns_a_pristine_copy_even_when_the_previous_copy_was_mutated(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\nspec: {tags: [a]}\n", encoding="utf-8")
    cache = BatchCache()
    first = cache.load_yaml(path)
    first["spec"]["tags"].append("mutated")
    second = cache.load_yaml(path)
    assert second == {"kind": "Agent", "spec": {"tags": ["a"]}}
    assert second is not first
    second["spec"]["tags"].append("again")
    assert cache.load_yaml(path)["spec"]["tags"] == ["a"]


def test_a_same_size_same_mtime_edit_is_not_served_stale(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("kind: Agent\nname: aaaa\n", encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    cache = BatchCache()
    assert cache.load_yaml(path)["name"] == "aaaa"
    path.write_text("kind: Agent\nname: bbbb\n", encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    assert path.stat().st_size == len("kind: Agent\nname: aaaa\n")
    assert path.stat().st_mtime_ns == stamp
    assert cache.load_yaml(path)["name"] == "bbbb"


def test_an_edit_replaces_the_entry_of_its_path(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    cache = BatchCache()
    for n in range(5):
        path.write_text(f"kind: Agent\nn: {n}\n", encoding="utf-8")
        assert cache.load_yaml(path)["n"] == n
    assert len(cache) == 1
