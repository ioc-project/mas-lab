#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl infra list / show / resolved."""

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
    """JSON on stdout. Deprecation and leftover-key warnings may precede it."""
    assert result.exit_code == 0, result.output
    text = result.output or ""
    start = text.find("{")
    assert start >= 0, text
    return json.loads(text[start:])


def test_infra_list_type_llm_lists_openai() -> None:
    result = CliRunner().invoke(app, ["infra", "list", "--type", "llm"])
    assert result.exit_code == 0, result.output
    assert "standard:openai" in result.output
    assert "kind=LLMProxy" in result.output
    assert "llm-cache" not in result.output


def test_infra_list_unknown_type_is_an_error() -> None:
    result = CliRunner().invoke(app, ["infra", "list", "--type", "nope"])
    assert result.exit_code != 0
    assert "unknown type" in (result.output or "") + (result.stderr or "")


def test_infra_list_default_omits_runtime_engine() -> None:
    result = CliRunner().invoke(app, ["infra", "list"])
    assert result.exit_code == 0, result.output
    assert "kind=RuntimeEngine" not in result.output
    typed = CliRunner().invoke(app, ["infra", "list", "--type", "runtime"])
    assert typed.exit_code == 0, typed.output
    assert "kind=RuntimeEngine" in typed.output
    assert "standard:runtime-default" in typed.output


def test_infra_show_help_documents_ref_not_session_flags() -> None:
    show = CliRunner().invoke(app, ["infra", "show", "--help"])
    assert show.exit_code == 0, show.output
    assert "REF" in show.output
    assert "--control" not in show.output
    resolved = CliRunner().invoke(app, ["infra", "resolved", "--help"])
    assert resolved.exit_code == 0, resolved.output
    assert "--control" in resolved.output


def test_infra_show_catalog_pipeline_includes_max_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["infra", "show", "standard:openai", "--pipeline", "--json"])
    payload = _json_payload(result)
    assert "api_base" not in payload["llm"][0]
    assert "tool" not in payload
    retry = next(step for step in payload["llm"][0]["steps"] if step["layer"] == "retry")
    assert retry["params"]["max_attempts"] == 4
    assert retry["default"] is True
    text = CliRunner().invoke(app, ["infra", "show", "standard:openai", "--pipeline"])
    assert text.exit_code == 0, text.output
    assert "max_attempts=4" in text.output
    assert "(default)" in text.output
    assert "local-tools" not in text.output


def test_infra_show_middleware_prints_listed_steps_not_fake_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["infra", "show", "standard:llm-cache", "--json"])
    payload = _json_payload(result)
    assert "llm" not in payload
    assert "tool" not in payload
    assert "agents" not in payload
    cache = next(step for step in payload["pipeline"] if step["layer"] == "llm_cache")
    assert cache["for"] == ["llm"]
    assert "default" not in cache
    assert not any(step.get("default") for step in payload["pipeline"])
    text = CliRunner().invoke(app, ["infra", "show", "standard:llm-cache", "--pipeline"])
    assert text.exit_code == 0, text.output
    assert "llm_cache" in text.output
    assert "max_attempts" not in text.output
    assert "(default)" not in text.output
    assert "use infra resolved" not in (text.output or "") + (text.stderr or "")
    connections = CliRunner().invoke(app, ["infra", "show", "standard:llm-cache", "--connections", "--json"])
    assert _json_payload(connections) == {}
    params = cache.get("params") or {}
    path = params.get("cache_path")
    assert not path
    assert "for=*" not in text.output


def test_infra_show_payload_does_not_invent_a_tool_destination() -> None:
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.infra.show import infra_show_payload

    payload = infra_show_payload(
        ResolvedInfra(
            llm_proxy={"pipeline": []},
            llm_proxies={"arm": {"api_base": "https://arm.example/v1"}},
            tool_server_registry={},
            tool_pipeline=[],
        )
    )
    assert payload.get("tool") == []
    assert payload["llm"][0]["name"] == "arm"


def test_infra_show_unnamed_agent_does_not_invent_an_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    agent = tmp_path / "anon.yaml"
    agent.write_text(
        "apiVersion: mas/v1\n"
        "kind: Agent\n"
        'spec:\n  description: "x"\n  models: [{model: gpt-4o-mini}]\n  context: {role: "x"}\n',
        encoding="utf-8",
    )
    payload = _json_payload(CliRunner().invoke(app, ["infra", "show", str(agent), "--json"]))
    assert all(row.get("id") != "agent" for row in payload.get("agents") or [])


def test_infra_show_runtime_engine_prints_kernel_fields_not_a_call_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["infra", "show", "standard:runtime-default", "--json"])
    payload = _json_payload(result)
    assert "llm" not in payload
    assert "tool" not in payload
    assert "pipeline" not in payload
    assert payload["runtime"]["queue_depth"] == 32
    assert payload["runtime"]["stream"] is False
    text = CliRunner().invoke(app, ["infra", "show", "standard:runtime-default"])
    assert text.exit_code == 0, text.output
    assert "queue_depth  32" in text.output
    assert "retry" not in text.output
    assert "(default)" not in text.output
    pipeline_only = CliRunner().invoke(app, ["infra", "show", "standard:runtime-default", "--pipeline", "--json"])
    assert pipeline_only.exit_code == 0, pipeline_only.output
    assert _json_payload(pipeline_only) == {}


def test_infra_show_connections_omits_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    payload = _json_payload(CliRunner().invoke(app, ["infra", "show", "standard:openai", "--connections", "--json"]))
    assert "api_base" in payload["llm"][0]
    assert "steps" not in payload["llm"][0]


def test_infra_show_llmproxy_file_is_catalog_not_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(tmp_path)
    result = CliRunner().invoke(app, ["infra", "show", str(proxy), "--json"])
    combined = (result.output or "") + (result.stderr or "")
    assert "use infra resolved" not in combined
    payload = _json_payload(result)
    assert payload["llm"][0]["name"] == "arm"
    assert "tool" not in payload


def test_infra_show_alias_warns_and_runs_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(tmp_path)
    result = CliRunner().invoke(app, ["infra", "show", "--json", "--infra-ref", str(proxy)])
    assert "use infra resolved" in (result.output or "") + (result.stderr or "")
    payload = _json_payload(result)
    assert payload["llm"][0]["name"] == "arm"


def test_infra_show_with_no_ref_warns_and_runs_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["infra", "show", "--json"])
    assert "use infra resolved" in (result.output or "") + (result.stderr or "")
    payload = _json_payload(result)
    assert "llm" in payload
    assert "tool" in payload


def test_infra_show_mas_file_warns_and_runs_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas = tmp_path / "qa.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: qa}\nspec:\n  agency:\n    agents:\n      - id: a\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["infra", "show", str(mas), "--json"])
    assert "use infra resolved" in (result.output or "") + (result.stderr or "")
    payload = _json_payload(result)
    assert payload["agents"][0]["id"] == "a"


def test_infra_show_agent_file_warns_and_runs_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    agent = tmp_path / "desk.yaml"
    agent.write_text(
        "apiVersion: mas/v1\n"
        "kind: Agent\n"
        "metadata: {name: desk}\n"
        'spec:\n  description: "x"\n  models: [{model: gpt-4o-mini}]\n  context: {role: "x"}\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["infra", "show", str(agent), "--json"])
    assert "use infra resolved" in (result.output or "") + (result.stderr or "")
    payload = _json_payload(result)
    assert payload["agents"][0]["id"] == "desk"


def test_infra_resolved_type_llm_omits_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(tmp_path)
    payload = _json_payload(
        CliRunner().invoke(app, ["infra", "resolved", "--json", "--type", "llm", "--infra-ref", str(proxy)])
    )
    assert "llm" in payload
    assert "tool" not in payload
    assert "agents" not in payload


def test_infra_resolved_json_lists_destination_chains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        ["infra", "resolved", "--json", "--infra-ref", str(proxy), "--infra-ref", str(cache)],
    )
    payload = _json_payload(result)
    assert payload["llm"]
    assert payload["llm"][0]["name"] == "arm"
    names = [step["middleware"] for step in payload["llm"][0]["steps"]]
    assert "llm_cache" in names
    assert "agents" in payload
    assert "tool" in payload


def _write_proxy(tmp_path: Path, *, extra: str = "") -> Path:
    proxy = tmp_path / "arm.yaml"
    proxy.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        "metadata: {name: arm}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy: {api_base: https://arm.example/v1, api_key_env: ARM_KEY}\n"
        "  models: {allowed: [m]}\n"
        f"{extra}",
        encoding="utf-8",
    )
    return proxy


def test_infra_resolved_applies_agent_control_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(tmp_path)
    agent = tmp_path / "desk.yaml"
    agent.write_text(
        "apiVersion: mas/v1\n"
        "kind: Agent\n"
        "metadata: {name: desk}\n"
        "spec:\n"
        '  description: "x"\n'
        "  models: [{model: gpt-4o-mini}]\n"
        '  context: {role: "x"}\n'
        "  control:\n"
        "    retry:\n"
        "      llm: {max_attempts: 9}\n"
        "    circuit_breaker:\n"
        "      failure_threshold: 4\n",
        encoding="utf-8",
    )
    payload = _json_payload(
        CliRunner().invoke(app, ["infra", "resolved", "--json", str(agent), "--infra-ref", str(proxy)])
    )
    arm = next(row for row in payload["llm"] if row["name"] == "arm")
    retry = next(step for step in arm["steps"] if step["layer"] == "retry")
    assert retry["params"]["max_attempts"] == 9
    assert "default" not in retry
    assert any(step["layer"] == "circuit_breaker" for step in arm["steps"])


def test_infra_resolved_applies_mas_llm_http_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "5")
    proxy = _write_proxy(tmp_path)
    payload = _json_payload(CliRunner().invoke(app, ["infra", "resolved", "--json", "--infra-ref", str(proxy)]))
    retry = next(step for step in payload["llm"][0]["steps"] if step["layer"] == "retry")
    assert retry["params"]["max_attempts"] == 6
    assert "default" not in retry


def test_infra_show_catalog_ignores_session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "5")
    payload = _json_payload(CliRunner().invoke(app, ["infra", "show", "standard:openai", "--pipeline", "--json"]))
    retry = next(step for step in payload["llm"][0]["steps"] if step["layer"] == "retry")
    assert retry["params"]["max_attempts"] == 4
    assert retry["default"] is True


def test_infra_show_lists_default_retry_when_pipeline_omits_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(tmp_path)
    runner = CliRunner()
    payload = _json_payload(runner.invoke(app, ["infra", "resolved", "--json", "--infra-ref", str(proxy)]))
    steps = payload["llm"][0]["steps"]
    retry = next(step for step in steps if step["layer"] == "retry")
    assert retry["for"] == ["llm"]
    assert retry["per"] == "destination"
    assert retry["default"] is True
    tool_retry = next(step for step in payload["tool"][0]["steps"] if step["layer"] == "retry")
    assert tool_retry["for"] == ["tool"]
    assert tool_retry["default"] is True
    text = runner.invoke(app, ["infra", "resolved", "--infra-ref", str(proxy)])
    assert text.exit_code == 0, text.output
    assert "retry  for=llm  per=destination" in text.output
    assert "max_attempts=4" in text.output
    assert "(default)" in text.output
    assert "retry  for=tool  per=all" in text.output


def test_infra_show_explicit_retry_is_not_marked_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    proxy = _write_proxy(
        tmp_path,
        extra=(
            "  pipeline:\n"
            "    - layer: retry\n"
            "      for: [llm]\n"
            "      per: destination\n"
            "      params: {max_attempts: 8, backoff_s: 0, jitter: false}\n"
        ),
    )
    runner = CliRunner()
    payload = _json_payload(runner.invoke(app, ["infra", "resolved", "--json", "--infra-ref", str(proxy)]))
    llm_retries = [step for step in payload["llm"][0]["steps"] if step["layer"] == "retry"]
    assert len(llm_retries) == 1
    assert "default" not in llm_retries[0]
    assert llm_retries[0]["params"]["max_attempts"] == 8
    text = runner.invoke(app, ["infra", "resolved", "--infra-ref", str(proxy)])
    assert text.exit_code == 0, text.output
    assert "max_attempts=8" in text.output
    llm_retry_lines = [line for line in text.output.splitlines() if "retry  for=llm" in line]
    assert llm_retry_lines
    assert all("(default)" not in line for line in llm_retry_lines)


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
    result = runner.invoke(app, ["infra", "resolved", "--infra-ref", str(proxy)])
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
    result = runner.invoke(app, ["infra", "resolved", "--json", str(mas)])
    payload = _json_payload(result)
    ids = {row["id"]: row["comm"] for row in payload["agents"]}
    assert ids["a"] == ["local"]  # the registry default; MAS spec.agent_comm is not read
    assert ids["b"] == ["local"]
    text = runner.invoke(app, ["infra", "resolved", str(mas)])
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
        ["infra", "resolved", "--infra-ref", str(proxy), "--infra-ref", str(tools)],
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
    args = ["infra", "resolved", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)]
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
    result = CliRunner().invoke(
        app, ["infra", "resolved", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)]
    )
    assert result.exit_code == 1
    assert "Application 'billing' is named by an agent but names no protocol" in result.output
    assert "Traceback" not in result.output


def test_infra_show_with_no_flavour_chain_prints_the_in_process_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, _flavour = _desk(tmp_path)
    result = CliRunner().invoke(app, ["infra", "resolved", "--json", str(mas), "--infra-ref", str(rows)])
    assert {row["id"]: row["comm"] for row in _json_payload(result)["agents"]} == {
        "triage": ["local"],
        "billing": ["local"],
        "notes": ["local"],
    }


def test_infra_show_reports_a_bad_identity_in_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_user_and_workspace(tmp_path, monkeypatch)
    mas, rows, flavour = _desk(tmp_path)
    rows.write_text(rows.read_text(encoding="utf-8").replace("acme/lab/desk", "acme/desk"), encoding="utf-8")
    result = CliRunner().invoke(
        app, ["infra", "resolved", str(mas), "--infra-ref", str(rows), "--flavour", str(flavour)]
    )
    assert result.exit_code == 1
    assert "identity must be a three-part org/ns/service name, got 'acme/desk'" in result.output
    assert "Traceback" not in result.output
