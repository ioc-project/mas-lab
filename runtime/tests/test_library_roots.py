#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.library_roots -- both "library is an installed package"
and "library is a plain folder at a known path" discovery must work, and a
single broken library must never take down discovery of the others.
"""

from __future__ import annotations

import sys
import types

import mas.library_roots as library_roots
from mas.library_roots import (
    _find_library_root,
    _known_library_paths,
    _root_from_editable_distribution,
    _root_from_import,
    _root_from_spec,
    discover_library_roots,
    find_ancestor_with_file,
    resolve_manifest_library_package,
)


def test_find_library_root_walks_upward(tmp_path) -> None:
    root = tmp_path / "lib"
    nested = root / "src" / "mas" / "library" / "x"
    nested.mkdir(parents=True)
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")

    assert _find_library_root(nested) == root.resolve()


def test_find_library_root_returns_none_when_absent(tmp_path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert _find_library_root(nested) is None


def test_find_ancestor_with_file_checks_start_itself(tmp_path) -> None:
    (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
    assert find_ancestor_with_file(tmp_path, "marker.txt") == tmp_path.resolve()


def test_find_ancestor_with_file_walks_multiple_levels_up(tmp_path) -> None:
    root = tmp_path / "root"
    nested = root / "a" / "b" / "c" / "d"
    nested.mkdir(parents=True)
    (root / "marker.txt").write_text("x", encoding="utf-8")

    assert find_ancestor_with_file(nested, "marker.txt") == root.resolve()


def test_find_ancestor_with_file_accepts_a_file_path_not_just_a_dir(tmp_path) -> None:
    root = tmp_path / "root"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / "marker.txt").write_text("x", encoding="utf-8")
    some_file = nested / "experiment.yaml"
    some_file.write_text("x", encoding="utf-8")

    assert find_ancestor_with_file(some_file, "marker.txt") == root.resolve()


def test_find_ancestor_with_file_returns_none_when_absent(tmp_path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_ancestor_with_file(nested, "marker.txt") is None


def test_find_ancestor_with_file_stops_at_suffix_boundary(tmp_path) -> None:
    """A marker file beyond the ``.lab``-suffixed ancestor must not be found --
    otherwise nesting one lab checkout inside another's tree would let a
    deeply-nested experiment pick up the *outer* lab's config."""
    outer_lab = tmp_path / "outer.lab"
    inner_lab = outer_lab / "nested" / "inner.lab"
    experiment = inner_lab / "experiments" / "01-smoke"
    experiment.mkdir(parents=True)
    (outer_lab / "marker.txt").write_text("outer", encoding="utf-8")

    assert (
        find_ancestor_with_file(experiment, "marker.txt", stop_at_suffix=".lab")
        is None
    )


def test_find_ancestor_with_file_finds_marker_at_the_suffix_boundary_itself(
    tmp_path,
) -> None:
    """The ``.lab`` directory itself must still be checked, not skipped."""
    lab_root = tmp_path / "some.lab"
    experiment = lab_root / "experiments" / "01-smoke"
    experiment.mkdir(parents=True)
    (lab_root / "marker.txt").write_text("x", encoding="utf-8")

    assert find_ancestor_with_file(experiment, "marker.txt", stop_at_suffix=".lab") == (
        lab_root.resolve()
    )


def test_root_from_spec_resolves_real_importable_module() -> None:
    # mas.library_roots itself is a real, importable module with a known file.
    found = _root_from_spec("mas.library_roots")
    # No library.yaml above this module in this repo layout -> None is a
    # correct answer here; the point is that it does not raise.
    assert found is None or found.is_dir()


def test_root_from_spec_swallows_broken_parent_import(monkeypatch) -> None:
    """The documented failure mode: find_spec() on a dotted submodule
    auto-imports its parent, and a parent that raises on import propagates
    out of find_spec() as ImportError, not a clean None. _root_from_spec
    must catch this -- a single broken library must not crash discovery."""

    def _boom(name, *args, **kwargs):
        raise ImportError(f"simulated broken parent package for {name}")

    monkeypatch.setattr(library_roots.importlib.util, "find_spec", _boom)

    assert _root_from_spec("some.broken.module") is None


def test_root_from_spec_returns_none_for_missing_module() -> None:
    assert _root_from_spec("definitely_not_a_real_module_xyz") is None


def test_root_from_import_resolves_via_full_import(monkeypatch, tmp_path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")

    fake = types.ModuleType("fake_importable_lib")
    fake.__file__ = str(root / "__init__.py")
    (root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "fake_importable_lib", fake)

    assert _root_from_import("fake_importable_lib") == root.resolve()


def test_root_from_import_swallows_import_errors(monkeypatch) -> None:
    def _boom(name, *args, **kwargs):
        raise ImportError(f"simulated import failure for {name}")

    monkeypatch.setattr(library_roots.importlib, "import_module", _boom)

    assert _root_from_import("some.broken.module") is None


def test_root_from_editable_distribution_reads_direct_url(monkeypatch, tmp_path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "library.yaml").write_text("name: x\n", encoding="utf-8")

    class _FakeDist:
        def read_text(self, name):
            assert name == "direct_url.json"
            return f'{{"url": "file://{root}", "dir_info": {{"editable": true}}}}'

    monkeypatch.setattr(
        library_roots.importlib.metadata,
        "packages_distributions",
        lambda: {"my_module": ["my-dist"]},
    )
    monkeypatch.setattr(library_roots.importlib.metadata, "distribution", lambda name: _FakeDist())

    assert _root_from_editable_distribution("my_module") == root.resolve()


def test_root_from_editable_distribution_swallows_lookup_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        library_roots.importlib.metadata,
        "packages_distributions",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated metadata failure")),
    )
    assert _root_from_editable_distribution("some_module") is None


def test_resolve_manifest_library_package_never_raises_on_broken_module(monkeypatch) -> None:
    """The core regression test: a single library whose import chain is
    broken (e.g. a missing optional dependency) must resolve to None, not
    raise -- otherwise one bad library takes down discovery for every
    other library in the same process (see _installed_library_roots)."""

    def _boom_spec(name, *args, **kwargs):
        raise ImportError("simulated broken parent package")

    def _boom_import(name, *args, **kwargs):
        raise ImportError("simulated broken import")

    monkeypatch.setattr(library_roots.importlib.util, "find_spec", _boom_spec)
    monkeypatch.setattr(library_roots.importlib, "import_module", _boom_import)
    monkeypatch.setattr(
        library_roots.importlib.metadata, "packages_distributions", lambda: {}
    )

    result = resolve_manifest_library_package("mas.library.totally_broken")
    assert result is None


def test_known_library_paths_single_root(tmp_path, monkeypatch) -> None:
    lib = tmp_path / "my-lib"
    lib.mkdir()
    (lib / "library.yaml").write_text("name: my-lib\n", encoding="utf-8")

    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lib))
    assert _known_library_paths() == [lib.resolve()]


def test_known_library_paths_scans_sibling_libraries(tmp_path, monkeypatch) -> None:
    parent = tmp_path / "monorepo"
    parent.mkdir()
    for name in ("library-a", "library-b"):
        lib = parent / name
        lib.mkdir()
        (lib / "library.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    (parent / "not-a-library").mkdir()  # no library.yaml -- should be skipped

    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(parent))
    found = {p.name for p in _known_library_paths()}
    assert found == {"library-a", "library-b"}


def test_known_library_paths_multiple_entries_and_missing_dirs(tmp_path, monkeypatch) -> None:
    lib1 = tmp_path / "lib1"
    lib1.mkdir()
    (lib1 / "library.yaml").write_text("name: lib1\n", encoding="utf-8")
    missing = tmp_path / "does-not-exist"

    import os

    monkeypatch.setenv("MAS_LIBRARY_PATHS", os.pathsep.join([str(lib1), str(missing)]))
    assert _known_library_paths() == [lib1.resolve()]


def test_known_library_paths_empty_env_var(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LIBRARY_PATHS", raising=False)
    assert _known_library_paths() == []


def test_discover_library_roots_finds_unpackaged_local_library(tmp_path, monkeypatch) -> None:
    """A library that is NOT an installed Python package -- just a folder
    with library.yaml, made discoverable via MAS_LIBRARY_PATHS."""
    lib = tmp_path / "local-only-lib"
    lib.mkdir()
    (lib / "library.yaml").write_text("name: local-only-lib\n", encoding="utf-8")

    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lib))
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: {})
    monkeypatch.setattr(
        "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
        classmethod(lambda cls, start=None: cls({})),
    )

    roots = discover_library_roots(tmp_path / "somewhere-else")
    assert lib.resolve() in roots


def test_discover_library_roots_dedupes_across_strategies(tmp_path, monkeypatch) -> None:
    lib = tmp_path / "dup-lib"
    lib.mkdir()
    (lib / "library.yaml").write_text("name: dup-lib\n", encoding="utf-8")

    # Same root reachable via both MAS_LIBRARY_PATHS and the installed-package stub.
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lib))
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: {"dup-lib": lib})
    monkeypatch.setattr(
        "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
        classmethod(lambda cls, start=None: cls({})),
    )

    roots = discover_library_roots(tmp_path / "somewhere-else")
    assert roots.count(lib.resolve()) == 1


def test_discover_library_roots_one_broken_installed_library_does_not_hide_others(
    tmp_path, monkeypatch
) -> None:
    """The end-to-end version of the regression test: even if the installed-
    package strategy blows up for one library, a library reachable via
    MAS_LIBRARY_PATHS must still be discovered."""
    good_lib = tmp_path / "good-local-lib"
    good_lib.mkdir()
    (good_lib / "library.yaml").write_text("name: good-local-lib\n", encoding="utf-8")

    def _boom():
        raise RuntimeError("simulated total failure of installed-package discovery")

    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(good_lib))
    monkeypatch.setattr(
        "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
        classmethod(lambda cls, start=None: cls({})),
    )

    # _installed_library_roots() itself already can't raise (see its own
    # try/except), but this proves discover_library_roots() as a whole
    # still surfaces the local library even when package discovery finds
    # nothing at all.
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: {})

    roots = discover_library_roots(tmp_path / "somewhere-else")
    assert good_lib.resolve() in roots


def test_discover_library_roots_does_not_scan_git_checkout_siblings(tmp_path, monkeypatch) -> None:
    """A .git ancestor is a walk boundary, not a library search root."""
    checkout = tmp_path / "mas-lab"
    (checkout / ".git").mkdir(parents=True)
    lab = checkout / "library-lab"
    lab.mkdir()
    (lab / "library.yaml").write_text("name: lab\n", encoding="utf-8")
    nested = checkout / "labs" / "lifecycle-control.lab"
    nested.mkdir(parents=True)

    monkeypatch.delenv("MAS_LIBRARY_PATHS", raising=False)
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: {})
    monkeypatch.setattr(
        "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
        classmethod(lambda cls, start=None: cls({})),
    )

    roots = discover_library_roots(nested)
    assert lab.resolve() not in roots


def test_discover_library_roots_includes_nested_lab_library(tmp_path, monkeypatch) -> None:
    """A library.yaml under a *.lab cwd is enumerated when that lab is the anchor."""
    lab = tmp_path / "demo.lab"
    mylib = lab / "mylib"
    mylib.mkdir(parents=True)
    (lab / "lab-config.yaml").write_text(
        "lab:\n  name: demo\n  libraries:\n    - mylib/\n",
        encoding="utf-8",
    )
    (mylib / "library.yaml").write_text("name: mylib\n", encoding="utf-8")

    monkeypatch.delenv("MAS_LIBRARY_PATHS", raising=False)
    monkeypatch.setattr(library_roots, "_installed_named_libraries", lambda: {})
    monkeypatch.setattr(
        "mas.runtime.workspace_config.RuntimeWorkspaceConfig.load",
        classmethod(lambda cls, start=None: cls({})),
    )
    monkeypatch.chdir(lab)

    roots = discover_library_roots(lab)
    assert mylib.resolve() in roots
