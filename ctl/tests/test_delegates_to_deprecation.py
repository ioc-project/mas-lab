#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``delegates_to`` stays valid and executable; validation warns once."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.manifest.mas_agent_merge import (
    enrich_entry_agent_for_delegation,
    wire_entry_engine_delegation,
)
from mas.ctl.validate import validate_data
from mas.ctl.validate.schemas import load_schema
from mas.runtime.boundary.delegation.policy import delegation_targets
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tools import openai_tools


class _StubComm:
    def send(self, *args: object, **kwargs: object) -> str:
        return "ok"


def _mas(*, nodes: list[dict]) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "demo"},
        "spec": {
            "agency": {"agents": [{"id": str(node["id"])} for node in nodes]},
            "workflow": {
                "entry": str(nodes[0]["id"]),
                "nodes": nodes,
            },
        },
    }


def _delegates_warnings(result) -> list:
    return [issue for issue in result.issues if issue.level == "warning" and "delegates_to" in issue.message]


def test_schema_marks_delegates_to_deprecated() -> None:
    schema = load_schema("mas")
    node = schema["properties"]["spec"]["properties"]["workflow"]["properties"]["nodes"]["items"]
    assert node["properties"]["delegates_to"]["deprecated"] is True


def test_manifest_with_delegates_to_validates_and_warns_once_with_node_ids() -> None:
    mas = _mas(
        nodes=[
            {"id": "broker", "delegates_to": ["flights", "hotels"]},
            {"id": "flights"},
            {"id": "hotels", "delegates_to": ["concierge"]},
            {"id": "concierge"},
        ]
    )
    result = validate_data(mas, kind="mas", strict=True, resolve_refs=False)
    assert result.ok
    warnings = _delegates_warnings(result)
    assert len(warnings) == 1
    message = warnings[0].message
    assert "deprecated" in message
    assert "still supported" in message
    assert "no removal date" in message
    assert "broker" in message
    assert "hotels" in message
    assert "flights" not in message.split("nodes:", 1)[-1]
    assert "concierge" not in message.split("nodes:", 1)[-1]


def test_second_validation_warns_again() -> None:
    mas = _mas(nodes=[{"id": "broker", "delegates_to": ["flights"]}, {"id": "flights"}])
    first = _delegates_warnings(validate_data(mas, kind="mas", strict=True, resolve_refs=False))
    second = _delegates_warnings(validate_data(mas, kind="mas", strict=True, resolve_refs=False))
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].message == second[0].message


def test_workflow_kind_warns_once_with_node_ids() -> None:
    data = {
        "apiVersion": "workflow/v1",
        "kind": "Workflow",
        "metadata": {"name": "demo"},
        "spec": {
            "entry": "broker",
            "nodes": [
                {"id": "broker", "delegates_to": ["flights"]},
                {"id": "flights"},
            ],
        },
    }
    result = validate_data(data, kind="workflow", strict=True, resolve_refs=False)
    assert result.ok
    warnings = _delegates_warnings(result)
    assert len(warnings) == 1
    assert "broker" in warnings[0].message
    assert "still supported" in warnings[0].message


def test_manifest_without_delegates_to_has_no_warning() -> None:
    mas = _mas(nodes=[{"id": "broker"}, {"id": "flights"}])
    result = validate_data(mas, kind="mas", strict=True, resolve_refs=False)
    assert result.ok
    assert _delegates_warnings(result) == []


def test_empty_delegates_to_does_not_warn() -> None:
    mas = _mas(
        nodes=[
            {"id": "broker", "delegates_to": ["flights"]},
            {"id": "flights", "delegates_to": []},
            {"id": "hotels", "delegates_to": []},
        ]
    )
    result = validate_data(mas, kind="mas", strict=True, resolve_refs=False)
    assert result.ok
    warnings = _delegates_warnings(result)
    assert len(warnings) == 1
    assert "broker" in warnings[0].message
    assert "flights" not in warnings[0].message.split("nodes:", 1)[-1]
    assert "hotels" not in warnings[0].message.split("nodes:", 1)[-1]


def test_unnamed_node_with_delegates_to_still_warns() -> None:
    mas = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "demo"},
        "spec": {
            "agency": {"agents": [{"id": "flights"}]},
            "workflow": {
                "entry": "flights",
                "nodes": [{"delegates_to": ["flights"]}, {"id": "flights"}],
            },
        },
    }
    result = validate_data(mas, kind="mas", strict=True, resolve_refs=False)
    warnings = _delegates_warnings(result)
    assert len(warnings) == 1
    assert "nodes[0]" in warnings[0].message


def test_workflow_kind_warning_path_is_spec_nodes() -> None:
    data = {
        "apiVersion": "workflow/v1",
        "kind": "Workflow",
        "metadata": {"name": "demo"},
        "spec": {
            "entry": "broker",
            "nodes": [
                {"id": "broker", "delegates_to": ["flights"]},
                {"id": "flights"},
            ],
        },
    }
    result = validate_data(data, kind="workflow", strict=True, resolve_refs=False)
    assert _delegates_warnings(result)[0].path == "spec.nodes"


def test_overlay_that_sets_delegates_to_warns() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "add-peers"},
        "spec": {
            "target": {"kind": "MAS", "name": "demo"},
            "patch": {
                "workflow": {
                    "nodes": [{"id": "broker", "delegates_to": ["flights"]}],
                }
            },
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert result.ok
    warnings = _delegates_warnings(result)
    assert len(warnings) == 1
    assert warnings[0].path == "spec.patch.workflow.nodes"
    assert "broker" in warnings[0].message


def _two_agent_mas(tmp_path: Path, *, delegates: bool) -> Path:
    for name in ("alpha", "beta"):
        path = tmp_path / "agents" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""apiVersion: mas/v1
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
    nodes = (
        """      - id: alpha
        delegates_to:
          - beta
      - id: beta
"""
        if delegates
        else """      - id: alpha
      - id: beta
"""
    )
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        f"""apiVersion: mas/v1
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
{nodes}""",
        encoding="utf-8",
    )
    return mas_path


def test_compose_run_prints_delegates_to_warning(tmp_path: Path, capsys) -> None:
    mas_path = _two_agent_mas(tmp_path, delegates=True)
    compose_run(
        ComposeRequest(
            manifest=mas_path,
            workspace_root=tmp_path,
            infra_refs=["standard:openai"],
        )
    )
    err = capsys.readouterr().err
    assert "delegates_to is deprecated, still supported, no removal date" in err
    assert "alpha" in err
    assert err.count("delegates_to is deprecated") == 1


def test_compose_run_warns_again_on_a_second_compose(tmp_path: Path, capsys) -> None:
    mas_path = _two_agent_mas(tmp_path, delegates=True)
    req = ComposeRequest(
        manifest=mas_path,
        workspace_root=tmp_path,
        infra_refs=["standard:openai"],
    )
    compose_run(req)
    compose_run(req)
    err = capsys.readouterr().err
    assert err.count("delegates_to is deprecated") == 2


def test_compose_run_without_delegates_to_prints_no_warning(tmp_path: Path, capsys) -> None:
    mas_path = _two_agent_mas(tmp_path, delegates=False)
    compose_run(
        ComposeRequest(
            manifest=mas_path,
            workspace_root=tmp_path,
            infra_refs=["standard:openai"],
        )
    )
    assert "delegates_to is deprecated" not in capsys.readouterr().err


def test_compose_run_warns_when_overlay_sets_delegates_to(tmp_path: Path, capsys) -> None:
    mas_path = _two_agent_mas(tmp_path, delegates=False)
    overlay_path = tmp_path / "overlays" / "add-peers.yaml"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        """apiVersion: mas/v1
kind: Overlay
metadata:
  name: add-peers
spec:
  target:
    kind: MAS
    name: two-agent-fixture
  patch:
    workflow:
      entry: alpha
      nodes:
        - id: alpha
          delegates_to: [beta]
        - id: beta
""",
        encoding="utf-8",
    )
    compose_run(
        ComposeRequest(
            manifest=mas_path,
            workspace_root=tmp_path,
            overlay_paths=[overlay_path],
            infra_refs=["standard:openai"],
        )
    )
    err = capsys.readouterr().err
    assert "delegates_to is deprecated, still supported, no removal date" in err
    assert "alpha" in err
    assert err.count("delegates_to is deprecated") == 1


def test_delegates_to_still_wires_the_same_tools(tmp_path: Path) -> None:
    peer_yaml = tmp_path / "agents" / "alpha.yaml"
    peer_yaml.parent.mkdir(parents=True)
    peer_yaml.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "alpha"},
                "spec": {"description": "Alpha specialist for numeric baselines."},
            }
        ),
        encoding="utf-8",
    )
    agent = {
        "metadata": {"name": "entry"},
        "spec": {"description": "Entry orchestrator."},
    }
    mas = {
        "spec": {
            "agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}],
            "workflow": {
                "entry": "entry",
                "nodes": [{"id": "entry", "delegates_to": ["alpha", "beta"]}],
            },
        }
    }
    assert delegation_targets(mas, agent_id="entry") == ["alpha", "beta"]
    enriched = enrich_entry_agent_for_delegation(agent, mas, mas_base_dir=tmp_path)
    engine = LiveLlmEngine(manifest=enriched, use_tool_loop=True)
    wire_entry_engine_delegation(
        engine,
        enriched,
        tmp_path,
        comm=_StubComm(),
        entry_agent_id="entry",
        mas_config=mas,
        mas_base_dir=tmp_path,
    )
    tools = openai_tools(
        enriched,
        agent_id="entry",
        peer_descriptions=engine.delegation_peer_descriptions,
    )
    by_name = {item["function"]["name"]: item for item in tools}
    assert "delegate_to_alpha" in by_name
    assert "Alpha specialist" in by_name["delegate_to_alpha"]["function"]["description"]
    assert by_name["delegate_to_beta"]["function"]["description"] == "Delegate a sub-task to agent beta."
    assert engine.delegation._allowed == frozenset({"alpha", "beta"})
