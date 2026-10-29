#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.runtime.spec.source."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from mas.runtime.spec.source import (
    clear_yaml_file_cache,
    load_yaml_file,
    resolve_manifest_source,
    resolve_ref_with_search,
    resolve_yaml_path,
    resolve_yaml_source,
)


def test_resolve_yaml_path_relative(tmp_path: Path) -> None:
    f = tmp_path / "agent.yaml"
    f.write_text("kind: Agent\n", encoding="utf-8")
    assert resolve_yaml_path("agent.yaml", tmp_path) == f.resolve()


def test_resolve_yaml_source_inline_first(tmp_path: Path) -> None:
    inline = {"steps": [{"name": "a"}]}
    out = resolve_yaml_source(inline=inline, anchor=tmp_path)
    assert out == inline


def test_resolve_yaml_source_ref(tmp_path: Path) -> None:
    f = tmp_path / "pipe.yaml"
    yaml.safe_dump({"steps": [{"name": "x"}]}, f.open("w"))
    out = resolve_yaml_source(ref="pipe.yaml", anchor=tmp_path)
    assert out["steps"][0]["name"] == "x"


def test_resolve_manifest_source_str_path(tmp_path: Path) -> None:
    f = tmp_path / "agent.yaml"
    f.write_text("kind: Agent\n", encoding="utf-8")
    data = resolve_manifest_source(str(f), anchor=tmp_path)
    assert data["kind"] == "Agent"


def test_resolve_ref_with_search(tmp_path: Path) -> None:
    sub = tmp_path / "infra"
    sub.mkdir()
    f = sub / "local.yaml"
    f.write_text("kind: Infra\n", encoding="utf-8")
    path = resolve_ref_with_search("local", tmp_path, search_dirs=[sub])
    assert path == f.resolve()


def test_resolve_yaml_path_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_yaml_path("missing.yaml", tmp_path)


def test_load_yaml_file(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    assert load_yaml_file(f) == {"a": 1}


def test_load_yaml_file_does_not_share_the_cached_mapping(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    data = load_yaml_file(f)
    data["a"] = 2
    assert load_yaml_file(f) == {"a": 1}


def test_load_yaml_file_sees_a_rewrite(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    assert load_yaml_file(f) == {"a": 1}
    f.write_text("a: 3\n", encoding="utf-8")
    assert load_yaml_file(f) == {"a": 3}


def test_load_yaml_file_keeps_the_parse_when_only_mtime_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    clear_yaml_file_cache()
    assert load_yaml_file(f) == {"a": 1}
    parsed = {"count": 0}
    real = yaml.safe_load

    def wrapped(text: object, *args: object, **kwargs: object) -> object:
        parsed["count"] += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr("mas.runtime.spec.source.yaml.safe_load", wrapped)
    os.utime(f, (f.stat().st_atime + 5, f.stat().st_mtime + 5))
    assert load_yaml_file(f) == {"a": 1}
    assert parsed["count"] == 0


def test_load_yaml_file_sees_a_size_change_with_mtime_held(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    clear_yaml_file_cache()
    assert load_yaml_file(f) == {"a": 1}
    mtime_ns = f.stat().st_mtime_ns
    f.write_text("a: 99\n", encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, mtime_ns))
    assert f.stat().st_mtime_ns == mtime_ns
    assert load_yaml_file(f) == {"a": 99}


def test_load_yaml_file_sees_a_same_size_rewrite_with_mtime_held(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    clear_yaml_file_cache()
    assert load_yaml_file(f) == {"a": 1}
    mtime_ns = f.stat().st_mtime_ns
    size = f.stat().st_size
    f.write_text("a: 9\n", encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, mtime_ns))
    assert f.stat().st_mtime_ns == mtime_ns
    assert f.stat().st_size == size
    assert load_yaml_file(f) == {"a": 9}


def test_clear_yaml_file_cache_empties_the_cache(tmp_path: Path) -> None:
    from mas.runtime.spec.source import _YAML_CACHE

    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    load_yaml_file(f)
    assert _YAML_CACHE
    clear_yaml_file_cache()
    assert not _YAML_CACHE
