#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON Schema + plugin-shape gates for every kind: Library manifest.

These tests exist so a wrong ``plugins:`` shape cannot pass just because
the runtime happened to load *some* plugins.  Schema and parser both
require a flat list of ``{type, name, module, class}`` entries — not a
mapping keyed by type.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_SKIP_PARTS = {".venv", ".venv-test", "node_modules", "ui", "site", "__pycache__"}


def _library_yaml_paths() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(_ROOT.rglob("library.yaml")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("kind") == "Library":
            paths.append(path)
    return paths


@pytest.mark.parametrize(
    "path",
    _library_yaml_paths(),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_library_manifest_strict_schema(path: Path) -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.validate import validate_file

    result = validate_file(path, kind="library", strict=True, resolve_refs=False)
    assert result.ok, result.issues


@pytest.mark.parametrize(
    "path",
    _library_yaml_paths(),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_library_plugins_is_a_list_and_types_cover_entries(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    plugins = doc.get("plugins")
    if plugins is None:
        return
    assert isinstance(plugins, list), f"{path}: plugins: must be a list of declarations, not {type(plugins).__name__}"
    declared_types = {str(t) for t in (doc.get("types") or [])}
    for index, entry in enumerate(plugins):
        assert isinstance(entry, dict), f"{path}: plugins[{index}] must be a mapping"
        plugin_type = entry.get("type")
        assert plugin_type, f"{path}: plugins[{index}] missing type:"
        assert plugin_type in declared_types, f"{path}: plugin type {plugin_type!r} is not listed in types:"
        assert entry.get("name"), f"{path}: plugins[{index}] missing name:"
        factory = entry.get("factory") or (entry.get("attributes") or {}).get("factory")
        has_class = entry.get("module") and entry.get("class")
        assert has_class or factory, (
            f"{path}: plugins[{index}] needs module+class, or factory: (module:function returning metrics)"
        )


@pytest.mark.parametrize(
    "path",
    [
        p
        for p in _library_yaml_paths()
        if "library-lab" in p.parts
        or "library-standard" in p.parts
        or "library-skills" in p.parts
        or "library-ioa" in p.parts
    ],
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_packaged_library_plugin_classes_import(path: Path, monkeypatch) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    monkeypatch.syspath_prepend(str(path.parent.parent))
    for entry in doc.get("plugins") or []:
        module = importlib.import_module(str(entry["module"]))
        assert hasattr(module, str(entry["class"])), f"{path}: {entry['module']}:{entry['class']} is not importable"


def test_plugin_extras_are_bare_extra_names() -> None:
    """``extra:`` is the extra name only; the wheel name is resolved at load."""
    for path in _library_yaml_paths():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for index, entry in enumerate(doc.get("plugins") or []):
            extra = entry.get("extra")
            if not extra:
                continue
            assert "[" not in str(extra), (
                f"{path}: plugins[{index}].extra must be a bare extra name, not a pip spec ({extra!r})"
            )


def test_library_packages_are_unique() -> None:
    """Library dirs that are their own plugin package share one sys.path namespace."""
    owners: dict[str, Path] = {}
    for path in _library_yaml_paths():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = path.parent.name
        if any(str(e.get("module", "")).startswith(name + ".") for e in doc.get("plugins") or []):
            assert name not in owners, f"package {name!r}: {owners[name]} and {path}"
            owners[name] = path
