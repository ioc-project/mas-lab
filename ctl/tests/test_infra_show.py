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


def _json_payload(result) -> dict:
    """JSON on stdout. ``resolve_flavour`` may print a leftover-key warning on stderr."""
    assert result.exit_code == 0, result.output
    body = "\n".join(line for line in result.output.splitlines() if not line.startswith("warning:"))
    return json.loads(body)


def test_infra_show_json_lists_destination_chains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    payload = _json_payload(result)
    assert payload["llm"]
    assert payload["llm"][0]["name"] == "arm"
    names = [step["middleware"] for step in payload["llm"][0]["steps"]]
    assert "llm_cache" in names
    assert "agents" in payload
    assert "tool" in payload


def test_infra_show_text_names_the_leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_infra_show_prints_the_library_default_link_not_mas_agent_comm(
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
    payload = _json_payload(result)
    ids = {row["id"]: row["comm"] for row in payload["agents"]}
    assert ids["a"] == ["local"]  # the registry default; MAS spec.agent_comm is not read
    assert ids["b"] == ["local"]
    text = runner.invoke(app, ["infra", "show", str(mas)])
    assert text.exit_code == 0, text.output
    assert "slim" not in text.output
    assert "(none)" not in text.output


def test_infra_show_strips_url_userinfo_and_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def _desk(tmp_path: Path) -> tuple[Path, Path, Path]:
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: desk}\nspec:\n  agency:\n    agents:\n"
        "      - id: triage\n      - id: billing\n      - id: notes\n"
        "  workflow:\n    entry: triage\n    nodes:\n      - id: triage\n        delegates_to: [billing]\n"
        "      - id: billing\n",
        encoding="utf-8",
    )
    rows = tmp_path / "rows.yaml"
    rows.write_text(
        "apiVersion: infra/v1\nkind: Application\nmetadata: {name: desk}\nspec:\n  endpoints:\n"
        "    desk-node: {protocol: slim, url: 'http://127.0.0.1:46357', identity: acme/lab/desk, secret_env: S,\n"
        "                usage: [connect, listen]}\n"
        "    desk-a2a: {protocol: a2a-framing, usage: [connect, listen]}\n",
        encoding="utf-8",
    )
    flavour = tmp_path / "flavour.yaml"
    flavour.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  bind:\n    agent_comm: [desk-node, desk-a2a]\n    usage: [connect, listen]\n"
        "    agents:\n      notes: {usage: connect}\n",
        encoding="utf-8",
    )
    return mas, rows, flavour


def test_infra_show_reads_the_flavour_chain_and_names_each_agent_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, flavour = _desk(tmp_path)
    runner = CliRunner()
    args = ["infra", "show", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)]
    result = runner.invoke(app, [*args, "--json"])
    agents = {row["id"]: row for row in _json_payload(result)["agents"]}
    assert agents["triage"] == {"id": "triage", "comm": ["slim", "a2a-framing"], "identity": "acme/lab/desk/triage"}
    assert agents["billing"] == {"id": "billing", "comm": ["slim", "a2a-framing"], "identity": "acme/lab/desk/billing"}
    # dialled over the chain, served elsewhere, sends to no one: on the chain, with no name here
    assert agents["notes"] == {"id": "notes", "comm": ["slim", "a2a-framing"], "identity": ""}
    text = runner.invoke(app, args)
    assert text.exit_code == 0, text.output
    assert "triage  slim, a2a-framing  acme/lab/desk/triage" in text.output


def test_infra_show_refuses_an_agent_named_row_with_no_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, flavour = _desk(tmp_path)
    rows.write_text(rows.read_text(encoding="utf-8") + "    billing: {usage: [connect, listen]}\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["infra", "show", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)])
    assert result.exit_code == 1
    assert "Application 'billing' is named by an agent but names no protocol" in result.output
    assert "Traceback" not in result.output


def test_infra_show_with_no_flavour_chain_prints_the_in_process_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, _flavour = _desk(tmp_path)
    result = CliRunner().invoke(app, ["infra", "show", "--json", str(mas), "--infra-ref", str(rows)])
    assert {row["id"]: row["comm"] for row in _json_payload(result)["agents"]} == {
        "triage": ["local"],
        "billing": ["local"],
        "notes": ["local"],
    }


def test_infra_show_reports_a_bad_identity_in_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, flavour = _desk(tmp_path)
    rows.write_text(rows.read_text(encoding="utf-8").replace("acme/lab/desk", "acme/desk"), encoding="utf-8")
    result = CliRunner().invoke(app, ["infra", "show", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)])
    assert result.exit_code == 1
    assert "identity must be a three-part org/ns/service name, got 'acme/desk'" in result.output
    assert "Traceback" not in result.output
