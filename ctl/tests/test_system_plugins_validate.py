#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-ctl validate`` on ``spec.system_plugins`` and the deprecated ``{kind: system}`` spelling."""

from __future__ import annotations

from typing import Any

from mas.ctl.overlay.merge import merge_overlay
from mas.ctl.validate import validate_data


def _agent(**spec: Any) -> dict[str, Any]:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "desk"},
        "spec": {"description": "d", **spec},
    }


def _validate(doc: dict[str, Any]):
    return validate_data(doc, kind="agent", strict=True, resolve_refs=False)


def test_system_plugins_by_name_urn_and_settings_validate_clean() -> None:
    result = _validate(
        _agent(system_plugins=["inform_user", "mas.system_plugin.send", {"request_human_input": {"timeout": 5}}])
    )
    assert result.ok
    assert not [i for i in result.issues if i.level in {"error", "warning"}]


def test_unknown_system_plugin_is_an_error_with_its_path() -> None:
    result = _validate(_agent(system_plugins=["inform_user", "infrom_user"]))
    assert not result.ok
    [error] = [i for i in result.issues if i.level == "error"]
    assert error.path == "spec.system_plugins[1]"
    assert "unknown system plugin" in error.message


def test_enabled_false_of_an_unknown_system_plugin_is_an_error() -> None:
    result = _validate(_agent(system_plugins=[{"no_such_plugin": {"enabled": False}}]))
    assert not result.ok


def test_malformed_system_plugin_item_is_an_error() -> None:
    result = _validate(_agent(system_plugins=[{"a": {}, "b": {}}]))
    assert not result.ok


def test_kind_system_entry_is_accepted_and_warns_with_the_replacement() -> None:
    result = _validate(_agent(tools=[{"kind": "system", "name": "inform_user", "params": {"max_message_length": 9}}]))
    assert result.ok
    [warning] = [i for i in result.issues if i.level == "warning"]
    assert warning.path == "spec.tools[0]"
    assert "spec.system_plugins" in warning.message
    assert "'inform_user'" in warning.message


def test_kind_system_entry_with_an_unknown_name_is_an_error() -> None:
    result = _validate(_agent(tools=[{"kind": "system", "name": "nope"}]))
    assert not result.ok


def test_kind_system_entry_without_a_name_is_an_error() -> None:
    result = _validate(_agent(tools=[{"kind": "system"}]))
    assert not result.ok
    assert any(i.level == "error" and i.path == "spec.tools[0]" and "needs a name" in i.message for i in result.issues)


def test_overlay_patch_with_kind_system_warns() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "patch"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {"tools": {"$op": {"add": [{"kind": "system", "name": "inform_user"}]}}},
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert any(i.level == "warning" and "spec.system_plugins" in i.message for i in result.issues)


def test_overlay_add_of_unknown_system_plugin_is_an_error() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "patch"},
        "spec": {"target": {"kind": "Agent"}, "patch": {"system_plugins": {"$op": {"add": ["nope"]}}}},
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert not result.ok
    [error] = [i for i in result.issues if i.level == "error"]
    assert error.path == "spec.patch.system_plugins.$op.add[0]"
    assert "unknown system plugin" in error.message


def test_overlay_kind_system_add_without_a_name_is_an_error() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "patch"},
        "spec": {"target": {"kind": "Agent"}, "patch": {"tools": {"$op": {"add": [{"kind": "system"}]}}}},
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert not result.ok
    assert any(i.level == "error" and "needs a name" in i.message for i in result.issues)


def test_overlay_kind_system_add_of_unknown_name_is_an_error() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "patch"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {"tools": {"$op": {"add": [{"kind": "system", "name": "nope"}]}}},
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert not result.ok
    assert any(i.level == "error" and "unknown system plugin" in i.message for i in result.issues)


def test_overlay_removes_a_system_plugin_by_any_of_its_names() -> None:
    base = _agent(system_plugins=[{"subagents": {"templates": []}}, "inform_user"])
    for name in ("create_subagent", "mas.system_plugin.subagents", "subagents", "spawn_subagent"):
        overlay = {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "patch"},
            "spec": {"target": {"kind": "Agent"}, "patch": {"system_plugins": {"$op": {"remove": [name]}}}},
        }
        merged = merge_overlay(base, overlay)["spec"]["system_plugins"]
        assert merged == ["inform_user"], name
