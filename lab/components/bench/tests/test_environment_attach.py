#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.environment.attach import (
    control_refs_for_run,
    fixture_scope_key,
    write_fixture_llm_infra,
)


def test_control_refs_include_generated_path(tmp_path: Path) -> None:
    generated = tmp_path / "control.yaml"
    generated.write_text("kind: Control\n", encoding="utf-8")
    refs = control_refs_for_run(loaded=["budget.yaml"], control_path=generated)
    assert refs == ["budget.yaml", str(generated)]
    again = control_refs_for_run(loaded=[str(generated)], control_path=generated)
    assert again == [str(generated)]
    empty = control_refs_for_run(loaded=[], control_path=None)
    assert empty == []


def test_fixture_scope_key_is_instance_id() -> None:
    assert fixture_scope_key(scope="run", run_key="s/i1/r1", item_key="s/i1", scenario_key="s") == "s/i1/r1"
    assert fixture_scope_key(scope="item", run_key="s/i1/r1", item_key="s/i1", scenario_key="s") == "s/i1"
    assert fixture_scope_key(scope="scenario", run_key="s/i1/r1", item_key="s/i1", scenario_key="s") == "s"


def test_double_attach_keeps_one_environment_plugin(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import resolve_controls, session_flavour_with_controls
    from mas.lab.benchmark.environment.engine import write_environment_control

    path = write_environment_control(tmp_path / "control.yaml")
    flavour = session_flavour_with_controls({}, controls=[str(path)], search_dirs=[tmp_path])
    again = session_flavour_with_controls(flavour, controls=[str(path)], search_dirs=[tmp_path])
    rows = resolve_controls(again)
    governance = [item for row in rows for item in ((row.get("spec") or {}).get("governance") or [])]
    assert governance.count("environment@v1") == 1


def test_write_fixture_llm_infra_points_at_loopback(tmp_path: Path) -> None:
    path = write_fixture_llm_infra(tmp_path / "scripted-model.yaml", "http://127.0.0.1:18433")
    text = path.read_text(encoding="utf-8")
    assert "api_base: http://127.0.0.1:18433/v1" in text
    assert "kind: LLMProxy" in text
    assert "name: openai-direct" in text
    assert "- scripted" in text
    assert "- any" in text
    assert "default: scripted" in text
    assert "any: scripted" in text


def test_write_fixture_llm_infra_is_keyless_and_never_fills_the_store(tmp_path: Path) -> None:
    """The proxy row names an optional secret: an empty store writes the row and stays empty."""
    import yaml
    from mas.runtime.secrets import SecretRef, prepare_manifest
    from secret_fakes import bound_secrets

    with bound_secrets({}) as resolver:
        path = write_fixture_llm_infra(tmp_path / "m.yaml", "http://127.0.0.1:1")
        assert not resolver.describe(SecretRef(name="lab")).present
    doc = prepare_manifest(yaml.safe_load(path.read_text(encoding="utf-8")))
    key = doc["spec"]["proxy"]["api_key"]
    assert isinstance(key, SecretRef) and key.name == "lab" and key.optional
