#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Smoke tests for ``mas-ctl compile``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from mas.ctl.cli.commands.compile import compile_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_1 = REPO_ROOT / "docs/tutorials/01-building-an-agent"
TUTORIAL_2 = REPO_ROOT / "docs/tutorials/02-creating-a-mas"


@pytest.mark.parametrize(
    "kind,spec,assignment,expected",
    [
        (
            "InfraMiddleware",
            {"middleware": "llm_cache", "params": {"allow_write": False}},
            "infra:spec.params.allow_write=true",
            {"middleware": "llm_cache", "params": {"allow_write": True}},
        ),
        (
            "LLMProxy",
            {"proxy": {"api_base": "http://localhost/v1"}},
            'infra:spec.proxy.api_base="http://localhost:9000/v1"',
            {"proxy": {"api_base": "http://localhost:9000/v1"}},
        ),
        (
            "ToolServerRegistry",
            {"tool_servers": [{"id": "mcp", "protocol": "mcp", "url": "http://localhost/mcp"}]},
            'infra:spec.tool_servers[id=mcp].url="http://localhost:9000/mcp"',
            {"tool_servers": [{"id": "mcp", "protocol": "mcp", "url": "http://localhost:9000/mcp"}]},
        ),
        (
            "Application",
            {"endpoints": {"peer": {"protocol": "a2a", "url": "http://localhost/agent"}}},
            'infra:spec.endpoints["peer"].url="http://localhost:9000/agent"',
            {"endpoints": {"peer": {"protocol": "a2a", "url": "http://localhost:9000/agent"}}},
        ),
        ("RuntimeEngine", {"cache": {"read": True}}, "infra:spec.cache.read=false", {"cache": {"read": False}}),
        (
            "Tool",
            {"parameters": [{"name": "amount", "type": "integer", "required": False}]},
            "tool:spec.parameters[*].required=true",
            {"parameters": [{"name": "amount", "type": "integer", "required": True}]},
        ),
        (
            "Flavour",
            {"tools": {"remote_tools_enabled": False}},
            "flavour:spec.tools.remote_tools_enabled=true",
            {"tools": {"remote_tools_enabled": True}},
        ),
    ],
)
def test_compile_generic_manifest_overrides(kind, spec, assignment, expected, tmp_path: Path) -> None:
    manifest = tmp_path / "source.yaml"
    if kind == "Tool":
        implementation = {"kind": "python", "module_path": "./tool.py", "class_name": "SampleTool"}
        spec = {**spec, "impl": implementation}
        expected = {**expected, "impl": implementation}
    original = yaml.safe_dump(
        {
            "apiVersion": "infra/v1" if kind not in {"Tool", "Flavour"} else "mas/v1",
            "kind": kind,
            "metadata": {"name": "sample"},
            "spec": spec,
        }
    )
    manifest.write_text(original, encoding="utf-8")
    result = CliRunner().invoke(compile_cmd, [str(manifest), "--override", assignment, "--no-header"])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["spec"] == expected
    assert manifest.read_text(encoding="utf-8") == original


def test_compile_agent_root_can_patch_envelope() -> None:
    result = CliRunner().invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "--override",
            "agent:metadata.name=renamed",
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["metadata"]["name"] == "renamed"


def test_compile_rejects_generic_manifest_root() -> None:
    result = CliRunner().invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "--override",
            "manifest:metadata.name=renamed",
        ],
    )
    assert result.exit_code != 0
    assert "not a session node" in result.output
    assert ".agent" in result.output


def test_compile_creates_schema_declared_missing_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "cache.yaml"
    manifest.write_text(
        "apiVersion: infra/v1\nkind: InfraMiddleware\nmetadata:\n  name: cache\n"
        "spec:\n  middleware: llm_cache\n  params:\n    allow_read: true\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        compile_cmd,
        [str(manifest), "--override", "infra:spec.params.allow_write=true", "--no-header"],
    )
    assert result.exit_code == 0, result.output
    spec = yaml.safe_load(result.output)["spec"]
    assert spec["params"]["allow_read"] is True
    assert spec["params"]["allow_write"] is True

    flavour = tmp_path / "flavour.yaml"
    flavour.write_text(
        "apiVersion: mas/v1\nkind: Flavour\nmetadata:\n  name: local\n"
        "spec:\n  tools:\n    remote_tools_enabled: false\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        compile_cmd,
        [str(flavour), "--override", "flavour:spec.capabilities.remote_eval=true", "--no-header"],
    )
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.output)
    assert doc["spec"]["tools"]["remote_tools_enabled"] is False
    assert doc["spec"]["capabilities"]["remote_eval"] is True


@pytest.mark.parametrize(
    "schema_kind,document,assignment",
    [
        (
            "library",
            {"apiVersion": "mas/v1", "kind": "Library", "name": "sample", "description": "base"},
            "library:description=updated",
        ),
        (
            "experiment",
            {"experiment": {"name": "base", "application": {"app": "sample"}}},
            "experiment:experiment.name=updated",
        ),
        (
            "pipeline",
            {"pipeline": {"name": "base", "steps": [{"type": "compute_ci"}]}},
            "pipeline:pipeline.name=updated",
        ),
        ("config", {"defaults": {"model": "any"}}, "config:defaults.model=updated"),
    ],
)
def test_compile_flat_and_wrapped_manifests(schema_kind, document, assignment, tmp_path: Path) -> None:
    manifest = tmp_path / "source.yaml"
    manifest.write_text(yaml.safe_dump(document), encoding="utf-8")
    result = CliRunner().invoke(
        compile_cmd,
        [
            str(manifest),
            "--kind",
            schema_kind,
            "--override",
            assignment,
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "updated" in result.output


@pytest.mark.parametrize(
    "assignment",
    [
        'infra:spec.params.allow_write="false"',
        "infra:spec.params.allow_write=3",
        "agent:spec.params.allow_write=true",
    ],
)
def test_compile_middleware_rejects_invalid_override(assignment, tmp_path: Path) -> None:
    manifest = tmp_path / "cache.yaml"
    manifest.write_text(
        "apiVersion: infra/v1\nkind: InfraMiddleware\nmetadata:\n  name: cache\n"
        "spec:\n  middleware: llm_cache\n  params:\n    allow_write: false\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(compile_cmd, [str(manifest), "--override", assignment])
    assert result.exit_code != 0


def test_compile_cli_stdout_tutorial_1() -> None:
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "-o",
            str(TUTORIAL_1 / "overlays/tools.yaml"),
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.output)
    assert doc["kind"] == "Agent"
    tools = doc["spec"]["tools"]
    refs = [t["ref"] for t in tools if isinstance(t, dict) and "ref" in t]
    system_tools = {t["name"] for t in tools if isinstance(t, dict) and t.get("kind") == "system"}
    assert "samples:tools/web-search.tool.yaml" in refs
    assert system_tools == {"request_human_input", "inform_user"}


def test_compile_cli_applies_override_after_file_overlay() -> None:
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "--override",
            'agent:spec.context.role="override role"',
            "--no-header",
        ],
    )

    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.output)
    assert doc["spec"]["context"]["role"] == "override role"


def test_compile_cli_applies_mas_agent_selector_to_inlined_agent(tmp_path: Path) -> None:
    manifest = tmp_path / "mas.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "mas/v1",
                "kind": "MAS",
                "metadata": {"name": "selector-test"},
                "spec": {
                    "agency": {
                        "agents": [
                            {
                                "apiVersion": "mas/v1",
                                "kind": "Agent",
                                "id": "qa",
                                "metadata": {"name": "qa"},
                                "spec": {
                                    "description": "QA agent",
                                    "context": {"role": "base"},
                                },
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        compile_cmd,
        [
            str(manifest),
            "--override",
            'mas:spec.agency.agents[id=qa].spec.context.role="selected"',
            "--layout",
            "bundle",
            "--no-defaults",
            "--no-header",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.output)
    assert doc["spec"]["agency"]["agents"][0]["spec"]["context"]["role"] == "selected"


def test_compile_cli_session_agent_path_patches_named_mas_agent(tmp_path: Path) -> None:
    manifest = tmp_path / "mas.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "mas/v1",
                "kind": "MAS",
                "metadata": {"name": "selector-test"},
                "spec": {
                    "agency": {
                        "agents": [
                            {
                                "apiVersion": "mas/v1",
                                "kind": "Agent",
                                "id": "qa",
                                "metadata": {"name": "qa"},
                                "spec": {
                                    "description": "QA agent",
                                    "context": {"role": "base"},
                                },
                            },
                            {
                                "apiVersion": "mas/v1",
                                "kind": "Agent",
                                "id": "planner",
                                "metadata": {"name": "planner"},
                                "spec": {
                                    "description": "Planner",
                                    "context": {"role": "base"},
                                },
                            },
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        compile_cmd,
        [
            str(manifest),
            "--override",
            '.mas.agent[qa].spec.context.role="selected"',
            "--layout",
            "bundle",
            "--no-defaults",
            "--no-header",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    agents = yaml.safe_load(result.output)["spec"]["agency"]["agents"]
    assert agents[0]["spec"]["context"]["role"] == "selected"
    assert agents[1]["spec"]["context"]["role"] == "base"


def test_compile_cli_rejects_standalone_agent_path_on_mas(tmp_path: Path) -> None:
    manifest = tmp_path / "mas.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "mas/v1",
                "kind": "MAS",
                "metadata": {"name": "team"},
                "spec": {"agency": {"agents": [{"id": "qa", "ref": "agents/qa.yaml"}]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        compile_cmd,
        [str(manifest), "--override", ".agent.spec.context.role=reviewer", "--no-validate"],
    )

    assert result.exit_code != 0
    assert ".mas.agent" in result.output


def test_compile_cli_writes_agent_file(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "compiled.yaml"
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "-o",
            str(TUTORIAL_1 / "overlays/skills.yaml"),
            "-O",
            str(out),
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["spec"]["skills"] == ["answer-formatting"]


def test_compile_cli_mas_tree_and_bundle(tmp_path: Path) -> None:
    runner = CliRunner()
    tree_dir = tmp_path / "tree"
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_2 / "mas.yaml"),
            "-o",
            str(TUTORIAL_2 / "overlays/linear.yaml"),
            "-O",
            str(tree_dir),
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tree_dir / "mas.yaml").is_file()
    assert (tree_dir / "agents/schedule-agent/agent.yaml").is_file()

    bundle = tmp_path / "team.yaml"
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_2 / "mas.yaml"),
            "-o",
            str(TUTORIAL_2 / "overlays/linear.yaml"),
            "--layout",
            "bundle",
            "-O",
            str(bundle),
            "--no-header",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(bundle.read_text(encoding="utf-8"))
    agents = doc["spec"]["agency"]["agents"]
    assert agents[0]["kind"] == "Agent"
    assert agents[0]["metadata"]["name"] == "moderator"


def test_compile_cli_tree_to_yaml_file_fails() -> None:
    runner = CliRunner()
    result = runner.invoke(
        compile_cmd,
        [
            str(TUTORIAL_1 / "agent.yaml"),
            "--layout",
            "tree",
            "-O",
            "out.yaml",
        ],
    )
    assert result.exit_code == 1
    assert "directory" in result.output.lower()
