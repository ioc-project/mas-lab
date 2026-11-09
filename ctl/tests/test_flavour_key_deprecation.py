#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Leftover Flavour keys are accepted, warned about once, and ignored."""

from __future__ import annotations

from mas.ctl.session.flavour import resolve_flavour
from mas.ctl.validate import validate_data
from mas.ctl.validate.schemas import load_schema


def _flavour(*, spec: dict) -> dict:
    return {"apiVersion": "flavour/v1", "kind": "Flavour", "metadata": {"name": "desk"}, "spec": spec}


def _warnings(result) -> list:
    return [issue for issue in result.issues if issue.level == "warning"]


def test_schema_marks_leftover_flavour_keys_deprecated() -> None:
    props = load_schema("flavour")["properties"]["spec"]["properties"]
    for key in ("agent_comm", "tools", "telemetry", "observability"):
        assert props[key]["deprecated"] is True
    assert props["control"].get("deprecated") is not True
    assert "bind" not in props or props["bind"].get("deprecated") is not True


def test_unread_keys_and_observability_warn_once() -> None:
    result = validate_data(
        _flavour(
            spec={
                "agent_comm": {"protocol": "agent-local"},
                "tools": {"exposure_protocol": "mcp"},
                "control": {"budget": {}},
                "telemetry": {"backend": "file"},
                "observability": ["native"],
            }
        ),
        kind="flavour",
        strict=True,
        resolve_refs=False,
    )
    assert result.ok
    warnings = _warnings(result)
    assert len(warnings) == 1
    message = warnings[0].message
    assert warnings[0].path == "spec"
    assert (
        "agent_comm, tools, telemetry, observability, control are deprecated and ignored; nothing reads them" in message
    )


def test_only_observability_is_deprecated_and_ignored() -> None:
    result = validate_data(
        _flavour(spec={"observability": ["native"]}), kind="flavour", strict=True, resolve_refs=False
    )
    assert result.ok
    [warning] = _warnings(result)
    assert "observability is deprecated and ignored; nothing reads it" in warning.message


def test_bind_only_flavour_does_not_warn() -> None:
    result = validate_data(
        _flavour(spec={"bind": {"agent_comm": ["desk-node"]}}), kind="flavour", strict=True, resolve_refs=False
    )
    assert result.ok
    assert _warnings(result) == []


def test_overlay_setting_leftover_keys_warns() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "desk"},
        "spec": {
            "target": {"kind": "Flavour"},
            "patch": {"agent_comm": {"protocol": "agent-local"}, "observability": ["native"]},
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    warnings = [issue for issue in _warnings(result) if "deprecated" in issue.message]
    assert len(warnings) == 1
    assert warnings[0].path == "spec.patch"
    assert "agent_comm, observability are deprecated and ignored; nothing reads them" in warnings[0].message


def test_bundled_flavours_are_quiet(capsys) -> None:
    """The default flavour of every run is ours: it must not warn the user."""
    resolve_flavour(None)
    resolve_flavour("local")
    assert capsys.readouterr().err == ""


def test_flavour_file_warns_once_on_stderr(tmp_path, capsys) -> None:
    path = tmp_path / "f.yaml"
    path.write_text(
        "apiVersion: mas/v1\nkind: Flavour\nmetadata: {name: f}\n"
        "spec:\n  tools: {allowed: ['*']}\n  observability: [native]\n",
        encoding="utf-8",
    )
    resolve_flavour(str(path))
    err = capsys.readouterr().err
    assert err.count("warning:") == 1
    assert "tools, observability are deprecated and ignored; nothing reads them" in err
