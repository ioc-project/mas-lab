#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl infra show text and JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from mas.ctl.cli.main import app


def _isolate_user_and_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not read the process CWD workspace (XDG is isolated in conftest)."""
    monkeypatch.chdir(tmp_path)


def test_infra_show_json_lists_destination_chains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = tmp_path / "arm.yaml"
    proxy.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        "metadata: {name: arm}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy: {api_base: https://arm.example/v1, api_key_env: ARM_KEY}\n"
        "  models: {allowed: [m]}\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache.yaml"
    cache.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        "metadata: {name: cache}\n"
        "spec:\n"
        "  middleware: llm_cache\n"
        "  for: llm\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["infra", "show", "--json", "--infra-ref", str(proxy), "--infra-ref", str(cache)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["llm"]
    assert payload["llm"][0]["name"] == "arm"
    names = [step["middleware"] for step in payload["llm"][0]["steps"]]
    assert "llm_cache" in names
    assert "agents" in payload
    assert "tool" in payload


def test_infra_show_text_names_the_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = tmp_path / "arm.yaml"
    proxy.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        "metadata: {name: arm}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy: {api_base: https://arm.example/v1, api_key_env: ARM_KEY}\n"
        "  models: {allowed: [m]}\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["infra", "show", "--infra-ref", str(proxy)])
    assert result.exit_code == 0, result.output
    assert "arm" in result.output
    assert "https://arm.example/v1" in result.output


def test_infra_show_reads_mas_agent_comm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas = tmp_path / "qa.yaml"
    mas.write_text(
        "apiVersion: mas/v1\n"
        "kind: MAS\n"
        "metadata: {name: qa}\n"
        "spec:\n"
        "  agent_comm: {type: slim}\n"
        "  agency:\n"
        "    agents:\n"
        "      - id: a\n"
        "      - id: b\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["infra", "show", "--json", str(mas)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ids = {row["id"]: row["comm"] for row in payload["agents"]}
    assert ids["a"] == ["slim"]
    assert ids["b"] == ["slim"]
    text = runner.invoke(app, ["infra", "show", str(mas)])
    assert text.exit_code == 0, text.output
    assert "slim" in text.output
    assert "(none)" not in text.output


def test_infra_show_strips_url_userinfo_and_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = tmp_path / "arm.yaml"
    proxy.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        "metadata: {name: arm}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy:\n"
        "    api_base: https://u:x@h.example/v1?api_key=y\n"
        "    api_key_env: ARM_KEY\n"
        "  models: {allowed: [m]}\n",
        encoding="utf-8",
    )
    tools = tmp_path / "tools.yaml"
    tools.write_text(
        "apiVersion: infra/v1\n"
        "kind: ToolServerRegistry\n"
        "metadata: {name: remote}\n"
        "spec:\n"
        "  tool_servers:\n"
        "    - id: mcp\n"
        "      protocol: mcp\n"
        "      url: https://h.example/mcp?token=z\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["infra", "show", "--infra-ref", str(proxy), "--infra-ref", str(tools)],
    )
    assert result.exit_code == 0, result.output
    assert "u:x" not in result.output
    assert "api_key=y" not in result.output
    assert "token=z" not in result.output
    assert "https://h.example/v1" in result.output
    assert "https://h.example/mcp" in result.output
