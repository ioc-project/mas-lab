#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ManifestStore."""

from __future__ import annotations

import pytest
from mas.lab.controller.manifest_store import ManifestStore


def test_manifest_store_crud(sample_lab, tmp_path):
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}

    exps = store.list_yaml_resources("demo", "experiments")
    assert any(e["name"] == "smoke" for e in exps)

    content = store.read_text("demo", "experiments", "smoke")
    assert "smoke" in content

    store.write_text("demo", "experiments", "new-exp", "metadata:\n  name: new\n")
    assert (sample_lab / "experiments" / "new-exp.yaml").exists()

    store.delete_resource("demo", "experiments", "new-exp")
    assert not (sample_lab / "experiments" / "new-exp.yaml").exists()

    configs = store.config_files("demo")
    assert configs["infra"]
    assert "flavours" in configs
    assert "workspace" in configs


def test_library_root_missing():
    store = ManifestStore(workspace=None)
    store._libraries = {}
    with pytest.raises(KeyError):
        store.library_root("missing")


def test_libraries_metadata(sample_lab):
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    libs = store.libraries()
    assert libs[0]["dir"] == "demo"


def test_read_missing_resource(sample_lab):
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    with pytest.raises(FileNotFoundError):
        store.read_text("demo", "experiments", "nope")


def test_nested_yaml_resource(sample_lab):
    nested = sample_lab / "experiments" / "suite" / "case.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("metadata:\n  name: case\n", encoding="utf-8")
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    text = store.read_text("demo", "experiments", "suite/case")
    assert "case" in text


def test_library_description_readme(sample_lab):
    (sample_lab / "README.md").write_text("# Demo lab\n", encoding="utf-8")
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    libs = store.libraries()
    assert libs[0]["description"].startswith("# Demo")


def test_config_files_picks_up_both_yaml_and_yml_suffixes(sample_lab):
    """config_files() consolidates 4 rglob scans into 2 suffix-filtered ones —
    both .yaml and .yml must still be discovered in flavours/ and infra/."""
    (sample_lab / "flavours" / "local.yml").write_text("metadata:\n  name: local-short-suffix\n", encoding="utf-8")
    (sample_lab / "infra" / "proxy.yml").write_text(
        "kind: LLMProxy\nmetadata:\n  name: proxy-short-suffix\n", encoding="utf-8"
    )
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}

    configs = store.config_files("demo")
    assert any(k.endswith("local.yml") for k in configs["flavours"])
    assert any(k.endswith("proxy.yml") for k in configs["infra"])
    assert any(k.endswith("tools.yaml") for k in configs["infra"])


def test_discover_from_workspace(tmp_path):
    from mas.lab.controller.lab_registry import LabRegistry, reset_lab_registry

    reset_lab_registry()

    class _Ws:
        _path = tmp_path
        _data = {"manifest_libraries": ["labs/ext"]}

    lib = tmp_path / "labs" / "ext"
    lib.mkdir(parents=True)
    (lib / "library.yaml").write_text("name: ext\n", encoding="utf-8")
    found = LabRegistry(_Ws()).library_paths()
    assert found["ext"] == lib.resolve()
    reset_lab_registry()


def test_write_creates_new_path(sample_lab):
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    path = store.write_text("demo", "experiments", "brand-new", "metadata:\n  name: brand-new\n")
    assert path.name == "brand-new.yaml"


def test_library_description_from_lab_yaml(sample_lab):
    (sample_lab / "lab-config.yaml").write_text("description: From lab yaml\n", encoding="utf-8")
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    libs = store.libraries()
    assert libs[0]["description"] == "From lab yaml"


def test_resolve_by_stem_match(sample_lab):
    nested = sample_lab / "experiments" / "sub" / "deep.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("metadata:\n  name: deep\n", encoding="utf-8")
    store = ManifestStore(workspace=None)
    store._libraries = {"demo": sample_lab}
    text = store.read_text("demo", "experiments", "deep")
    assert "deep" in text
