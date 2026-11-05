#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``workflow.nodes[].dispatch`` is accepted, ignored, and warned about once per validation."""

from __future__ import annotations

from pathlib import Path

from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.validate import validate_data
from mas.ctl.validate.schemas import load_schema


def _mas(*, nodes: list[dict]) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "demo"},
        "spec": {
            "agency": {"agents": [{"id": str(node["id"])} for node in nodes]},
            "workflow": {"entry": str(nodes[0]["id"]), "nodes": nodes},
        },
    }


def _dispatch_warnings(result) -> list:
    return [issue for issue in result.issues if issue.level == "warning" and "dispatch" in issue.message]


def test_schema_marks_dispatch_deprecated_and_still_accepts_it() -> None:
    schema = load_schema("mas")
    node = schema["properties"]["spec"]["properties"]["workflow"]["properties"]["nodes"]["items"]
    assert node["properties"]["dispatch"]["deprecated"] is True
    assert "ignored" in node["properties"]["dispatch"]["description"]


def test_dispatch_validates_and_warns_once_listing_the_nodes() -> None:
    mas = _mas(
        nodes=[
            {"id": "broker", "dispatch": "parallel"},
            {"id": "flights"},
            {"id": "hotels", "dispatch": "all"},
        ]
    )
    result = validate_data(mas, kind="mas", strict=True, resolve_refs=False)
    assert result.ok
    warnings = _dispatch_warnings(result)
    assert len(warnings) == 1
    message = warnings[0].message
    assert "deprecated and ignored" in message
    assert "parallel tool calls" in message
    listed = message.split("nodes:", 1)[-1]
    assert "broker" in listed and "hotels" in listed and "flights" not in listed
    assert warnings[0].path == "spec.workflow.nodes"


def test_a_node_id_is_listed_once() -> None:
    from mas.ctl.validate.deprecations import collect_dispatch_deprecation

    nodes = [{"id": "broker", "dispatch": "parallel"}, {"id": "broker", "dispatch": "all"}, {"dispatch": "all"}]
    data = {"spec": {"workflow": {"entry": "broker", "nodes": [{"id": "broker"}, *nodes]}}}
    [(_path, message)] = collect_dispatch_deprecation(data)
    assert message.endswith("set by nodes: broker, nodes[3]")


def test_no_dispatch_no_warning() -> None:
    result = validate_data(
        _mas(nodes=[{"id": "broker"}, {"id": "flights"}]), kind="mas", strict=True, resolve_refs=False
    )
    assert result.ok
    assert _dispatch_warnings(result) == []


def test_overlay_setting_dispatch_warns() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "fan"},
        "spec": {
            "target": {"kind": "MAS", "name": "demo"},
            "patch": {"workflow": {"nodes": [{"id": "broker", "dispatch": "parallel"}]}},
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    warnings = _dispatch_warnings(result)
    assert len(warnings) == 1
    assert warnings[0].path == "spec.patch.workflow.nodes"


def test_compose_run_prints_the_dispatch_warning_once(tmp_path: Path, capsys) -> None:
    for name in ("alpha", "beta"):
        path = tmp_path / "agents" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""apiVersion: agent/v1
kind: Agent
metadata:
  name: {name}
spec:
  description: test agent
  models:
    - model: gpt-4o-mini
""",
            encoding="utf-8",
        )
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        """apiVersion: mas/v1
kind: MAS
metadata:
  name: two-agent-fixture
spec:
  agency:
    agents:
      - id: alpha
        ref: agents/alpha.yaml
      - id: beta
        ref: agents/beta.yaml
  workflow:
    entry: alpha
    nodes:
      - id: alpha
        dispatch: parallel
      - id: beta
""",
        encoding="utf-8",
    )
    compose_run(ComposeRequest(manifest=mas_path, workspace_root=tmp_path, infra_refs=["standard:openai"]))
    err = capsys.readouterr().err
    assert err.count("dispatch is deprecated and ignored") == 1
    assert "set by nodes: alpha" in err
