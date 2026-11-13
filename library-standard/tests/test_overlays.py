#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library-standard overlays: index, shape, merge, packaged URI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

OVERLAYS = Path(__file__).resolve().parents[1] / "src" / "mas" / "library" / "standard" / "overlays"
CONTROLS = OVERLAYS.parent / "controls"
INDEX = OVERLAYS / "README.md"
_CONSTRAINT = ("observability", "governance")


def _overlay_yaml_files() -> list[Path]:
    return sorted(p for p in OVERLAYS.glob("*.yaml") if p.is_file())


def test_index_lists_every_overlay_file() -> None:
    readme = INDEX.read_text(encoding="utf-8")
    files = _overlay_yaml_files()
    assert files, "expected at least one overlay yaml"
    for path in files:
        assert path.name in readme, f"{path.name} missing from overlays/README.md"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        name = (doc.get("metadata") or {}).get("name")
        assert name, f"{path.name} has no metadata.name"
        assert str(name) in readme, f"{name} missing from overlays/README.md"


def test_overlay_dir_files_are_overlay_or_control() -> None:
    for path in _overlay_yaml_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        kind = doc.get("kind")
        assert kind in {"Overlay", "Control"}, path.name
        if kind == "Overlay":
            assert doc["apiVersion"] == "mas/v1"
            kind_target = (doc.get("spec") or {}).get("target", {}).get("kind")
            assert kind_target in {"Agent", "Control"}, path.name
            patch = (doc.get("spec") or {}).get("patch") or {}
            assert patch
            if kind_target == "Agent":
                for key in _CONSTRAINT:
                    assert key not in patch, path.name
            else:
                assert set(patch) <= {"observability", "governance"}
        else:
            assert doc["apiVersion"] == "control/v1"


def test_with_hardened_control_sets_governance_and_retry() -> None:
    doc = yaml.safe_load((CONTROLS / "with-hardened.yaml").read_text(encoding="utf-8"))
    names = [g if isinstance(g, str) else next(iter(g)) for g in doc["spec"]["governance"]]
    assert names == ["gov_no_undeclared_tool", "gov_no_undeclared_peer", "budget", "retry_on_error"]
    retry_on_error = next(
        item for item in doc["spec"]["governance"] if isinstance(item, dict) and "retry_on_error" in item
    )
    assert retry_on_error["retry_on_error"]["error_recovery_plugin"] == "retry_on_error"
    assert retry_on_error["retry_on_error"]["error_policy"]["transient"] == "retry"
    budget = next(item for item in doc["spec"]["governance"] if isinstance(item, dict) and "budget" in item)
    assert budget["budget"] == {"max_llm_calls": 50, "max_tool_calls": 100}
    assert set(doc["spec"]) == {"governance"}


def test_observability_native_sets_events_jsonl() -> None:
    doc = yaml.safe_load((CONTROLS / "observability-native.yaml").read_text(encoding="utf-8"))
    native = next(
        item for item in doc["spec"]["observability"] if isinstance(item, dict) and "mas.observability.native" in item
    )
    assert native["mas.observability.native"]["path"] == "traces/events.jsonl"


def test_hardened_control_validates() -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.validate import validate_file

    control = validate_file(CONTROLS / "with-hardened.yaml", kind="control")
    assert control.ok, control.issues


def test_pkg_refs_resolve_to_overlay_and_control_files() -> None:
    from mas.runtime.package_refs import resolve_path_ref

    for directory, name in (
        ("overlays", "production-memory.yaml"),
        ("controls", "with-hardened.yaml"),
        ("controls", "observability-native.yaml"),
        ("controls", "openclaw.yaml"),
    ):
        path = resolve_path_ref(f"pkg://mas.library.standard/{directory}/{name}", Path.cwd())
        assert path.is_file(), path
        assert path.name == name


def test_the_hardened_control_replaces_agent_governance() -> None:
    from mas.ctl.session.flavour_control import (
        apply_flavour_controls,
        attach_controls,
        control_lists_for_agent,
        control_row_from_path,
    )

    merged = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "telemetry"},
        "spec": {"governance": ["sample_governance"], "tools": ["get_metrics"]},
    }
    flavour: dict = {}
    row = control_row_from_path(CONTROLS / "with-hardened.yaml")
    attach_controls(flavour, [row])
    applied = apply_flavour_controls(merged, flavour, agent_id="telemetry", entry_id="telemetry", warn=False)
    assert applied["spec"]["governance"] == ["sample_governance"]
    lists = control_lists_for_agent([row], agent_id="telemetry", entry_id="telemetry")
    names = [g if isinstance(g, str) else next(iter(g)) for g in lists["governance"]]
    assert names == ["budget", "gov_no_undeclared_peer", "gov_no_undeclared_tool", "retry_on_error"]


def test_hardened_then_native_keeps_both() -> None:
    from mas.ctl.session.flavour_control import (
        apply_flavour_controls,
        attach_controls,
        control_lists_for_agent,
        control_row_from_path,
    )
    from mas.ctl.session.manifest_config import observability_config_from_manifest

    merged = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "telemetry"},
        "spec": {"governance": ["sample_governance"]},
    }
    flavour: dict = {}
    rows = [control_row_from_path(CONTROLS / name) for name in ("with-hardened.yaml", "observability-native.yaml")]
    attach_controls(flavour, rows)
    applied = apply_flavour_controls(merged, flavour, agent_id="telemetry", entry_id="telemetry", warn=False)
    assert applied["spec"]["governance"] == ["sample_governance"]
    assert "observability" not in applied["spec"]
    lists = control_lists_for_agent(rows, agent_id="telemetry", entry_id="telemetry")
    gov = [g if isinstance(g, str) else next(iter(g)) for g in lists["governance"]]
    assert "gov_no_undeclared_tool" in gov
    assert "gov_no_undeclared_peer" in gov
    native = lists["observability"][0]["mas.observability.native"]
    assert native["path"] == "traces/events.jsonl"
    cfg = observability_config_from_manifest(applied, flavour_spec=flavour, agent_id="telemetry")
    assert cfg.plugins == ["native"]


def test_controls_dir_holds_only_controls_that_name_no_absolute_path() -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.session.flavour_control import absolute_path_findings
    from mas.ctl.validate import validate_file

    files = sorted(CONTROLS.glob("*.yaml"))
    assert {path.name for path in files} == {"observability-native.yaml", "openclaw.yaml", "with-hardened.yaml"}
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["kind"] == "Control", path.name
        assert absolute_path_findings(doc) == [], path.name
        assert validate_file(path, kind="control").ok, path.name
