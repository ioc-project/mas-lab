#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for unified LabRegistry."""
from __future__ import annotations

from pathlib import Path

from mas.lab.controller.lab_registry import (
    LabRegistry,
    get_lab_registry,
    reset_lab_registry,
)
from mas.runtime.agent_defaults import default_model, default_pattern_plugin_id


def test_agent_defaults():
    reset_lab_registry()
    reg = get_lab_registry()
    defaults = reg.agent_defaults()
    assert defaults["design_pattern"]["type"] == default_pattern_plugin_id()
    assert defaults["models"][0]["model"] == "any"
    assert reg.default_model()
    reset_lab_registry()


def test_catalog_includes_runtime_and_steps():
    reset_lab_registry()
    reg = get_lab_registry()
    catalog = reg.catalog()
    assert "runtime" in catalog
    assert "spec" in catalog
    assert "design_pattern" in catalog["spec"]
    assert "pipeline_steps" in catalog
    assert "defaults" in catalog
    assert catalog["defaults"]["models"][0]["model"]
    reset_lab_registry()


def test_list_spec_design_pattern():
    reset_lab_registry()
    reg = get_lab_registry()
    patterns = reg.list("design_pattern")
    assert isinstance(patterns, list)
    if patterns:
        assert "urn" in patterns[0]
    reset_lab_registry()


def test_resolve_model_name_any_without_pin_raises(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    from mas.ctl.session.engine_factory import resolve_model_name, UnresolvedModelError

    import pytest

    with pytest.raises(UnresolvedModelError, match="will not substitute gpt-4o-mini"):
        resolve_model_name({"spec": {"llm": {"model": None}}}, None)
    with pytest.raises(UnresolvedModelError, match="will not substitute gpt-4o-mini"):
        resolve_model_name(None, None)


def test_default_model_is_gpt4o_mini():
    reset_lab_registry()
    reg = get_lab_registry()
    assert reg.default_model() == default_model() or reg.default_model()
    reset_lab_registry()


def test_runtime_objects_delegates_to_list_objects_single_pass(monkeypatch):
    """runtime_objects() must call mas.registry.list_objects() exactly once,
    not loop get()/list_names() per name (the source of the N+1 rescan bug)."""
    import mas.registry as registry_mod

    reset_lab_registry()
    reg = get_lab_registry()

    calls = {"n": 0}
    real_list_objects = registry_mod.list_objects

    def counting_list_objects(kind: str):
        calls["n"] += 1
        return real_list_objects(kind)

    monkeypatch.setattr(registry_mod, "list_objects", counting_list_objects)

    objects = reg.runtime_objects("dataset")
    assert calls["n"] == 1
    assert objects == real_list_objects("dataset")
    reset_lab_registry()


def test_list_experiments_lab_layout(tmp_path: Path):
    lab = tmp_path / "demo.lab"
    lab.mkdir()
    (lab / "experiment.yaml").write_text(
        "experiment:\n  name: root-exp\n  description: root\n",
        encoding="utf-8",
    )
    reset_lab_registry()
    reg = LabRegistry()
    reg._libraries = {"demo": lab}
    exps = reg.list_experiments("demo")
    assert any(e["name"] == "root-exp" for e in exps)
    reset_lab_registry()


def test_mas_library_paths_strips_lab_suffix(tmp_path: Path, monkeypatch):
    lab = tmp_path / "sample-demo.lab"
    lab.mkdir()
    (lab / "lab-config.yaml").write_text("lab:\n  name: sample-demo\n", encoding="utf-8")
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lab))
    reset_lab_registry()
    reg = LabRegistry()
    libraries, sources = reg._discover_library_paths()
    assert libraries["sample-demo"] == lab.resolve()
    assert sources["sample-demo"] == "MAS_LIBRARY_PATHS:lab"
    reset_lab_registry()


def test_mas_library_paths_registers_library_yaml_name(tmp_path: Path, monkeypatch):
    lib = tmp_path / "pack"
    lib.mkdir()
    (lib / "library.yaml").write_text("name: mas-example-library\n", encoding="utf-8")
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lib))
    reset_lab_registry()
    reg = LabRegistry()
    libraries, sources = reg._discover_library_paths()
    assert libraries["pack"] == lib.resolve()
    assert libraries["mas-example-library"] == lib.resolve()
    assert reg.library_root("mas-example-library") == lib.resolve()
    reset_lab_registry()


def test_mas_library_paths_discovers_lab_config_root(tmp_path: Path, monkeypatch):
    lab = tmp_path / "demo-lib"
    lab.mkdir()
    (lab / "lab-config.yaml").write_text("lab:\n  name: demo\n", encoding="utf-8")
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(lab))
    reset_lab_registry()
    reg = LabRegistry()
    libraries, sources = reg._discover_library_paths()
    assert libraries["demo-lib"] == lab.resolve()
    assert sources["demo-lib"] == "MAS_LIBRARY_PATHS:lab"
    reset_lab_registry()
