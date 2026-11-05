#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the manifest-library-relative path resolver added with the
registry refactor (``_resolve_in_library`` / ``resolve_path_ref``)."""

from __future__ import annotations

from pathlib import Path

import mas.runtime.package_refs as package_refs
import pytest
from mas.runtime.package_refs import _resolve_in_library, resolve_path_ref


def test_resolve_in_library_explicit_yaml(tmp_path: Path) -> None:
    target = tmp_path / "pipelines" / "native-to-otel-json.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    assert _resolve_in_library(tmp_path, "pipelines/native-to-otel-json.yaml") == target


def test_resolve_in_library_extension_implied(tmp_path: Path) -> None:
    target = tmp_path / "pipelines" / "native-to-otel-json.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    # `.yaml` omitted -> resolved via the extension-candidate branch.
    assert _resolve_in_library(tmp_path, "pipelines/native-to-otel-json") == target


def test_resolve_in_library_pipelines_and_extension_implied(tmp_path: Path) -> None:
    target = tmp_path / "pipelines" / "native-to-otel-json.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    # Neither `pipelines/` nor `.yaml` given -> resolved via the combined branch.
    assert _resolve_in_library(tmp_path, "native-to-otel-json") == target


def test_resolve_in_library_yml_extension(tmp_path: Path) -> None:
    target = tmp_path / "flow.yml"
    target.write_text("x", encoding="utf-8")
    assert _resolve_in_library(tmp_path, "flow") == target


def test_resolve_in_library_leading_slash_stripped(tmp_path: Path) -> None:
    target = tmp_path / "flow.yaml"
    target.write_text("x", encoding="utf-8")
    assert _resolve_in_library(tmp_path, "/flow.yaml") == target


def test_resolve_in_library_missing_returns_literal(tmp_path: Path) -> None:
    # Nothing exists -> literal lib_root / rel is returned (caller raises).
    assert _resolve_in_library(tmp_path, "nope") == tmp_path / "nope"


def test_resolve_path_ref_uses_library_scheme(tmp_path: Path, monkeypatch) -> None:
    lib_root = tmp_path / "lib"
    target = lib_root / "pipelines" / "flow.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        package_refs, "_manifest_library_root", lambda scheme, *a: lib_root if scheme == "telemetry" else None
    )

    assert resolve_path_ref("telemetry:flow", tmp_path) == target


def test_resolve_path_ref_relative_path(tmp_path: Path) -> None:
    out = resolve_path_ref("sub/thing.yaml", tmp_path)
    assert out == (tmp_path / "sub" / "thing.yaml").resolve()


def test_resolve_path_ref_absolute_path(tmp_path: Path) -> None:
    abs_path = (tmp_path / "abs.yaml").resolve()
    assert resolve_path_ref(str(abs_path), tmp_path) == abs_path


def test_resolve_path_ref_uses_workspace_registered_scheme(tmp_path: Path, monkeypatch) -> None:
    lib_root = tmp_path / "ws" / "mylib"
    target = lib_root / "tools" / "calc.tool.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    (lib_root / "library.yaml").write_text("name: mylib\n", encoding="utf-8")

    class _FakeWS:
        found = True
        root = tmp_path / "ws"
        manifest_libraries = {"mylib": "mylib"}

    _isolate_named_libraries(monkeypatch, workspace=_FakeWS())
    monkeypatch.chdir(tmp_path)

    assert resolve_path_ref("mylib:tools/calc.tool.yaml", tmp_path) == target


def test_resolve_path_ref_unknown_library_raises(tmp_path: Path, monkeypatch) -> None:
    _isolate_named_libraries(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LookupError, match="unknown library 'no-such-lib'"):
        resolve_path_ref("no-such-lib:thing.yaml", tmp_path)


def test_resolve_path_ref_pkg_without_resource_path_raises() -> None:
    with pytest.raises(ValueError):
        resolve_path_ref("pkg://somepackage", Path.cwd())


def _isolate_named_libraries(monkeypatch, *, installed=None, workspace=None) -> None:
    import mas.library_roots as library_roots

    monkeypatch.delenv("MAS_LIBRARY_PATHS", raising=False)
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: dict(installed or {}))
    if workspace is None:
        monkeypatch.setattr(
            "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
            classmethod(
                lambda cls, start=None: type("WS", (), {"found": False, "root": None, "manifest_libraries": {}})()
            ),
        )
    else:
        monkeypatch.setattr(
            "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
            classmethod(lambda cls, start=None: workspace),
        )


def _write_lab_library(lab: Path, lib_name: str, rel: str, *, listed: bool) -> Path:
    labs = ["  libraries:", f"    - {lib_name}/"] if listed else []
    (lab / "lab-config.yaml").write_text(
        "lab:\n  name: demo\n" + ("\n".join(labs) + "\n" if labs else ""),
        encoding="utf-8",
    )
    lib = lab / lib_name
    target = lib / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    (lib / "library.yaml").write_text(f"name: {lib_name}\n", encoding="utf-8")
    target.write_text("x", encoding="utf-8")
    return target


def test_resolve_path_ref_lab_local_listed_library(tmp_path: Path, monkeypatch) -> None:
    lab = tmp_path / "demo.lab"
    lab.mkdir()
    target = _write_lab_library(lab, "mylib", "thing.yaml", listed=True)
    _isolate_named_libraries(monkeypatch)
    monkeypatch.chdir(lab)

    assert resolve_path_ref("mylib:thing.yaml", lab) == target


def test_resolve_path_ref_lab_immediate_child_library(tmp_path: Path, monkeypatch) -> None:
    lab = tmp_path / "demo.lab"
    lab.mkdir()
    target = _write_lab_library(lab, "mylib", "thing.yaml", listed=False)
    _isolate_named_libraries(monkeypatch)
    monkeypatch.chdir(lab)

    assert resolve_path_ref("mylib:thing.yaml", lab) == target


def test_listed_lib_without_library_yaml_is_not_a_scheme(tmp_path: Path, monkeypatch) -> None:
    lab = tmp_path / "demo.lab"
    lib = lab / "lib"
    lib.mkdir(parents=True)
    rel = lib / "foo.yaml"
    rel.write_text("x", encoding="utf-8")
    (lab / "lab-config.yaml").write_text(
        "lab:\n  name: demo\n  libraries:\n    - lib/\n",
        encoding="utf-8",
    )
    _isolate_named_libraries(monkeypatch)
    monkeypatch.chdir(lab)

    with pytest.raises(LookupError, match="unknown library 'lib'"):
        resolve_path_ref("lib:foo.yaml", lab)
    assert resolve_path_ref("lib/foo.yaml", lab) == rel.resolve()


def test_resolve_path_ref_installed_library_when_no_local_or_workspace(tmp_path: Path, monkeypatch) -> None:
    installed = tmp_path / "installed-mylib"
    target = installed / "thing.yaml"
    target.parent.mkdir(parents=True)
    (installed / "library.yaml").write_text("name: mylib\n", encoding="utf-8")
    target.write_text("x", encoding="utf-8")
    _isolate_named_libraries(monkeypatch, installed={"mylib": installed})

    assert resolve_path_ref("mylib:thing.yaml", tmp_path) == target


def test_lab_local_library_wins_over_installed(tmp_path: Path, monkeypatch) -> None:
    lab = tmp_path / "demo.lab"
    lab.mkdir()
    local = _write_lab_library(lab, "mylib", "thing.yaml", listed=True)
    installed = tmp_path / "installed-mylib"
    other = installed / "thing.yaml"
    other.parent.mkdir(parents=True)
    (installed / "library.yaml").write_text("name: mylib\n", encoding="utf-8")
    other.write_text("installed", encoding="utf-8")
    _isolate_named_libraries(monkeypatch, installed={"mylib": installed})
    monkeypatch.chdir(lab)

    assert resolve_path_ref("mylib:thing.yaml", lab) == local
    assert resolve_path_ref("mylib:thing.yaml", lab).read_text(encoding="utf-8") == "x"
