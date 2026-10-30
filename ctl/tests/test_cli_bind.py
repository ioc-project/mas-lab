# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""URI launch-wiring shorthand expands into infra CLI overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.cli.bind_flags import BIND_ALIAS_WARNING, with_connects
from mas.ctl.infra.resolve import resolve_infra_refs
from mas.ctl.overrides import apply_cli_overrides, expand_connects, expand_listens
from mas.ctl.overrides.bind import combine_overrides
from mas.ctl.session.flavour import resolve_flavour
from mas.ctl.workspace.config import WorkspaceConfig


def test_expand_connects_a2a_is_infra_connect_not_a_flavour_switch() -> None:
    overrides = expand_connects(
        (
            "banking_assistant=a2a://127.0.0.1:8080/agents/banking_assistant/",
            "fraud_adjudicator=a2a://127.0.0.1:8080/agents/fraud_adjudicator/",
        )
    )
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraBundle",
        "metadata": {"name": "merged"},
        "spec": {"endpoints": {}, "tool_servers": []},
    }

    result = apply_cli_overrides(document, overrides, root="infra")

    assert result["spec"]["endpoints"]["banking_assistant"] == {
        "protocol": "a2a",
        "usage": "connect",
        "url": "http://127.0.0.1:8080/agents/banking_assistant/",
    }
    assert result["spec"]["endpoints"]["fraud_adjudicator"]["usage"] == "connect"
    assert all(item.startswith("infra:") for item in overrides)
    flavour = resolve_flavour("local", overrides=overrides)
    assert flavour.get("agent_comm", {}).get("protocol") == "agent-local"


def test_expand_connects_mcp_collapses_same_origin_into_one_tool_server() -> None:
    overrides = expand_connects(
        (
            "analyze_transaction_risk=mcp://127.0.0.1:8080/mcp#analyze_transaction_risk",
            "calculate_fraud_score=mcp://127.0.0.1:8080/mcp#calculate_fraud_score",
        )
    )

    assert len(overrides) == 1
    assert overrides[0].startswith("infra:spec.tool_servers[id=mcp-127.0.0.1-8080-mcp]=")
    assert "analyze_transaction_risk" in overrides[0]
    assert "calculate_fraud_score" in overrides[0]
    flavour = resolve_flavour("local", overrides=overrides)
    assert flavour.get("agent_comm", {}).get("protocol") == "agent-local"
    assert flavour.get("tools", {}).get("remote_tools_enabled") is False


def test_explicit_override_wins_over_connect() -> None:
    combined = combine_overrides(
        connects=("greeter=a2a://127.0.0.1:9002/",),
        overrides=("infra:spec.endpoints['greeter'].url=http://127.0.0.1:9009",),
    )
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraBundle",
        "metadata": {"name": "merged"},
        "spec": {"endpoints": {}, "tool_servers": []},
    }

    result = apply_cli_overrides(document, combined, root="infra")

    assert result["spec"]["endpoints"]["greeter"]["url"] == "http://127.0.0.1:9009"
    assert result["spec"]["endpoints"]["greeter"]["protocol"] == "a2a"
    assert result["spec"]["endpoints"]["greeter"]["usage"] == "connect"


def test_connect_creates_missing_mcp_tool_server() -> None:
    overrides = expand_connects(("lookup=mcp://127.0.0.1:9001/mcp#lookup",))
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraBundle",
        "metadata": {"name": "merged"},
        "spec": {"endpoints": {}, "tool_servers": [{"id": "local", "protocol": "local"}]},
    }

    result = apply_cli_overrides(
        document,
        overrides,
        root="infra",
    )

    servers = {item["id"]: item for item in result["spec"]["tool_servers"]}
    assert "local" in servers
    assert servers["mcp-127.0.0.1-9001-mcp"]["url"] == "http://127.0.0.1:9001/mcp"
    assert servers["mcp-127.0.0.1-9001-mcp"]["tools"] == ["lookup"]
    assert servers["mcp-127.0.0.1-9001-mcp"]["usage"] == "connect"


def test_bind_override_validates_when_model_defaults_are_unset(tmp_path: Path) -> None:
    infra_path = tmp_path / "a2a-agents.yaml"
    infra_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "infra/v1",
                "kind": "Application",
                "metadata": {"name": "a2a-agents"},
                "spec": {
                    "endpoints": {
                        "assistant": {
                            "protocol": "a2a",
                            "usage": "use-and-deploy",
                            "url": "http://127.0.0.1:9001",
                        },
                        "greeter": {
                            "protocol": "a2a",
                            "usage": "use-and-deploy",
                            "url": "http://127.0.0.1:9002",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_infra_refs(
        [str(infra_path)],
        anchor=tmp_path,
        workspace=WorkspaceConfig({}),
        overrides=list(expand_connects(("greeter=a2a://127.0.0.1:9002/",))),
    )

    assert resolved.applications["assistant"]["url"] == "http://127.0.0.1:9001"
    assert resolved.applications["assistant"]["usage"] == "use-and-deploy"
    assert resolved.applications["greeter"] == {
        "protocol": "a2a",
        "usage": "connect",
        "url": "http://127.0.0.1:9002/",
    }


def test_public_live_commands_expose_connect_option() -> None:
    import importlib

    from click.testing import CliRunner

    for module_name, attr in (
        ("mas.ctl.cli.commands.chat", "chat_cmd"),
        ("mas.ctl.cli.commands.compose", "compose_cmd"),
        ("mas.ctl.cli.commands.run_mas", "run_mas_cmd"),
        ("mas.ctl.cli.commands.serve", "serve_agent_cmd"),
        ("mas.ctl.cli.commands.tui", "tui_cmd"),
    ):
        result = CliRunner().invoke(getattr(importlib.import_module(module_name), attr), ["--help"])
        assert result.exit_code == 0, result.output
        assert "--connect NAME=URI" in result.output
        assert "--bind NAME=URI" not in result.output


def test_expand_listens_a2a_is_infra_listen_the_mirror_of_connect() -> None:
    overrides = expand_listens(("qa-agent=a2a://127.0.0.1:9006",))
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraBundle",
        "metadata": {"name": "merged"},
        "spec": {"endpoints": {}, "tool_servers": []},
    }

    result = apply_cli_overrides(document, overrides, root="infra")

    assert result["spec"]["endpoints"]["qa-agent"] == {
        "protocol": "a2a",
        "usage": "listen",
        "url": "http://127.0.0.1:9006/",
    }


def test_expand_listens_rejects_mcp() -> None:
    with pytest.raises(ValueError, match="mas-mcp"):
        expand_listens(("calc=mcp://127.0.0.1:9001/mcp",))


def test_combine_overrides_orders_connects_listens_then_explicit() -> None:
    combined = combine_overrides(
        connects=("peer=a2a://h:1/",), listens=("me=a2a://h:2/",), overrides=("infra:spec.x=1",)
    )
    assert "usage" in combined[0] and combined[-1] == "infra:spec.x=1"
    assert "listen" in combined[1]


_LIVE_COMMANDS = (
    ("mas.ctl.cli.commands.chat", "chat_cmd", False),
    ("mas.ctl.cli.commands.tui", "tui_cmd", False),
    ("mas.ctl.cli.commands.run_mas", "run_mas_cmd", False),
    ("mas.ctl.cli.commands.compose", "compose_cmd", True),
    ("mas.ctl.cli.commands.serve", "serve_agent_cmd", True),
)


def _live_cmd(module_name: str, attr: str):
    import importlib

    return getattr(importlib.import_module(module_name), attr)


def _live_args(tmp_path: Path, needs_manifest: bool, *flags: str) -> list[str]:
    if not needs_manifest:
        return list(flags)
    path = tmp_path / "agent.yaml"
    if not path.exists():
        path.write_text("kind: Agent\nmetadata:\n  name: x\n", encoding="utf-8")
    return [str(path), *flags]


def _captured(result) -> str:
    return f"{result.stderr or ''}{result.output}"


@pytest.mark.parametrize(("module_name", "attr", "needs_manifest"), _LIVE_COMMANDS)
def test_live_commands_bad_connect_is_usage_error(
    tmp_path: Path, module_name: str, attr: str, needs_manifest: bool
) -> None:
    from click.testing import CliRunner

    result = CliRunner().invoke(
        _live_cmd(module_name, attr),
        _live_args(tmp_path, needs_manifest, "--connect", "not-a-uri"),
    )
    assert result.exit_code == 2, result.output
    text = _captured(result)
    assert "Traceback" not in text
    assert "Invalid value" in text
    assert "--connect" in text
    assert "NAME=URI" in text


@pytest.mark.parametrize(("module_name", "attr", "needs_manifest"), _LIVE_COMMANDS)
def test_live_commands_bind_alias_warns(
    tmp_path: Path, module_name: str, attr: str, needs_manifest: bool
) -> None:
    from click.testing import CliRunner

    result = CliRunner().invoke(
        _live_cmd(module_name, attr),
        _live_args(tmp_path, needs_manifest, "--bind", "peer=a2a://127.0.0.1:9/"),
    )
    assert BIND_ALIAS_WARNING in _captured(result)


def test_connects_precede_binds() -> None:
    combined = with_connects(
        connects=("first=a2a://h:1/",),
        binds=("second=a2a://h:2/",),
    )
    assert "endpoints['first']" in combined[0]
    assert "endpoints['second']" in combined[1]


def test_live_command_connect_then_bind() -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd

    result = CliRunner().invoke(
        chat_cmd,
        [
            "--connect",
            "first=a2a://h:1/",
            "--bind",
            "second=a2a://h:2/",
        ],
    )
    text = _captured(result)
    assert BIND_ALIAS_WARNING in text
    assert "Traceback" not in text


def test_serve_exposes_listen_and_cache_read_only_flags() -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.serve import serve_agent_cmd

    result = CliRunner().invoke(serve_agent_cmd, ["--help"])
    assert result.exit_code == 0
    assert "--listen" in result.output and "--cache-read-only" in result.output
