#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the ``mas-lab telemetry`` CLI component (click CliRunner)."""

from __future__ import annotations

import json
import sys
import types

import pytest

click = pytest.importorskip("click")
from click.testing import CliRunner  # noqa: E402

from mas.library.telemetry.lab_cli import TelemetryCliComponent, telemetry_group  # noqa: E402
from tests.conftest import requires_otel  # noqa: E402

runner = CliRunner()


def _write_spans(path, names):
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "name": n,
                    "context": {"span_id": str(i), "trace_id": "t"},
                    "start_time": "2024-01-01T00:00:00.000000Z",
                    "end_time": "2024-01-01T00:00:01.000000Z",
                    "attributes": {"mas.boundary": n, "application_id": "app"},
                }
            )
            for i, n in enumerate(names)
        )
        + "\n"
    )


def test_kinds():
    result = runner.invoke(telemetry_group, ["kinds"])
    assert result.exit_code == 0
    assert "llm_call_start" in json.loads(result.output)


def test_inspect_and_verify(tmp_path):
    spans = tmp_path / "s.jsonl"
    _write_spans(spans, ["TaskCall", "AgentCall"])
    r1 = runner.invoke(telemetry_group, ["inspect", str(spans)])
    assert r1.exit_code == 0
    assert json.loads(r1.output)["spans"] == 2
    r2 = runner.invoke(telemetry_group, ["verify", str(spans), "--level", "L3"])
    assert r2.exit_code == 0  # no --fail → always 0


def test_compare(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_spans(a, ["TaskCall", "AgentCall"])
    _write_spans(b, ["TaskCall", "AgentCall"])
    result = runner.invoke(telemetry_group, ["compare", str(a), str(b)])
    assert result.exit_code == 0
    assert json.loads(result.output)["failed"] == 0


def test_push_dry_run(tmp_path):
    spans = tmp_path / "s.jsonl"
    _write_spans(spans, ["AgentCall"])
    # give the SDK-shaped spans real hex ids so OTLP conversion keeps them
    spans.write_text(
        json.dumps(
            {
                "name": "AgentCall",
                "context": {"span_id": "0x1", "trace_id": "0x2"},
                "start_time": "2024-01-01T00:00:00.000000Z",
                "end_time": "2024-01-01T00:00:01.000000Z",
                "attributes": {"mas.boundary": "AgentCall", "application_id": "app"},
            }
        )
        + "\n"
    )
    result = runner.invoke(
        telemetry_group,
        [
            "push",
            str(spans),
            "--endpoint",
            "http://x:4318",
            "--app-name",
            "app",
            "--dry-run",
            "--new-session-id",
            "--shift-to-now",
        ],
    )
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["status"] == "dry-run"
    assert body["session_id"].startswith("app_")


@requires_otel
def test_convert(tmp_path, events_path):
    out = tmp_path / "spans.jsonl"
    result = runner.invoke(
        telemetry_group,
        [
            "convert",
            str(events_path),
            "-o",
            str(out),
            "--app-name",
            "demo",
            "--new-session-id",
            "--shift-to-now",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["spans"] > 0


@requires_otel
def test_convert_realtime(tmp_path, events_path):
    import json as _json

    out = tmp_path / "spans.jsonl"
    result = runner.invoke(
        telemetry_group,
        ["convert", str(events_path), "-o", str(out), "--app-name", "demo", "--realtime"],
    )
    assert result.exit_code == 0
    names = {_json.loads(line)["name"] for line in out.read_text().splitlines() if line.strip()}
    assert not any(n.endswith(".graph") for n in names)
    assert any(n.startswith("topology.node.") for n in names)


def test_dump_and_list_apps(tmp_path, monkeypatch):
    # Fake clickhouse_connect so dump/list-apps run without a real ClickHouse.
    class _Rows:
        column_names = ["ServiceName", "spans"]
        result_rows = [["app-a", 3]]

    class _Client:
        def query(self, q):
            return _Rows()

    mod = types.ModuleType("clickhouse_connect")
    mod.get_client = lambda **kw: _Client()
    monkeypatch.setitem(sys.modules, "clickhouse_connect", mod)

    r1 = runner.invoke(telemetry_group, ["list-apps"])
    assert r1.exit_code == 0 and "app-a" in r1.output
    r2 = runner.invoke(
        telemetry_group, ["dump", "sess-1", "-o", str(tmp_path / "d.jsonl")]
    )
    assert r2.exit_code == 0
    assert json.loads(r2.output)["spans"] == 1


def test_component_register():
    grp = click.Group("mas-lab")
    name = TelemetryCliComponent().register(grp)
    assert name == "telemetry"
    assert "telemetry" in grp.commands
    assert "otel" in grp.commands  # legacy alias
    # idempotent
    assert TelemetryCliComponent().register(grp) is None


def test_component_register_replaces_lab_stub():
    grp = click.Group("mas-lab")
    stub = click.Group("telemetry")
    grp.add_command(stub)
    name = TelemetryCliComponent().register(grp)
    assert name == "telemetry"
    assert grp.commands["telemetry"] is telemetry_group


def test_component_register_keeps_lab_stub_commands_with_no_equivalent():
    # lab's native-trace `show` command (and similar) has no OTel equivalent
    # here — replacing the stub must not silently drop it.
    grp = click.Group("mas-lab")
    stub = click.Group("telemetry")

    @click.command("show")
    def native_show_cmd() -> None:
        pass

    @click.command("push")
    def stub_push_cmd() -> None:
        pass

    stub.add_command(native_show_cmd)
    stub.add_command(stub_push_cmd)  # name collides with telemetry_group's own `push`
    grp.add_command(stub)

    TelemetryCliComponent().register(grp)

    assert grp.commands["telemetry"].commands["show"] is native_show_cmd
    # library-telemetry's own `push` wins over the stub's same-named command.
    assert grp.commands["telemetry"].commands["push"] is not stub_push_cmd


def test_push_dry_run_defaults_localhost(tmp_path):
    spans = tmp_path / "spans.jsonl"
    _write_spans(spans, ["AgentCall"])
    result = runner.invoke(telemetry_group, ["push", str(spans), "--dry-run"])
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["status"] == "dry-run"
    assert "localhost:4318" in body["detail"]


def test_push_app_name_sets_service_name(tmp_path, monkeypatch):
    from mas.library.telemetry.collector import otlp

    spans = tmp_path / "s.jsonl"
    spans.write_text(
        json.dumps(
            {
                "name": "AgentCall",
                "context": {"span_id": "0x1", "trace_id": "0x2"},
                "start_time": "2024-01-01T00:00:00.000000Z",
                "end_time": "2024-01-01T00:00:01.000000Z",
                "attributes": {"mas.boundary": "AgentCall"},
                "resource": {"attributes": {"service.name": "original"}},
            }
        )
        + "\n"
    )
    seen = {}

    def fake_push(spans_, resource_attrs, endpoint, **kw):
        seen.update(resource_attrs)
        return {"spans": len(spans_), "batches": 1, "status": "dry-run", "detail": ""}

    monkeypatch.setattr(otlp, "_push", fake_push)
    result = runner.invoke(
        telemetry_group,
        ["push", str(spans), "--endpoint", "http://x:4318", "--app-name", "my-app"],
    )
    assert result.exit_code == 0
    assert seen["service.name"] == "my-app"


def _span(name, agent, **attrs):
    return {"name": name, "status": {"status_code": attrs.pop("status", "UNSET")},
            "attributes": {"mas.agent.id": agent, **attrs}}


def _write_conversation(path):
    spans = [
        _span("a.chat", "lead", **{"gen_ai.completion.0.content": "I will ask the specialist."}),
        _span("lookup.tool", "lead", **{"mas.tool.name": "lookup", "mas.tool.input": '{"q": 1}'}),
        _span("commit.tool", "lead", **{"mas.tool.name": "commit", "mas.tool.input": '{"action": "go"}'}),
        _span("b.chat", "specialist", status="ERROR", **{"gen_ai.completion.0.content": "Done."}),
    ]
    path.write_text("\n".join(json.dumps(s) for s in spans) + "\n")


def test_errors_counts_and_fails_on_request(tmp_path):
    spans = tmp_path / "s.jsonl"
    _write_conversation(spans)
    ok = runner.invoke(telemetry_group, ["errors", str(spans)])
    assert ok.exit_code == 0 and "ERROR spans    1" in ok.output
    assert runner.invoke(telemetry_group, ["errors", str(spans), "--fail"]).exit_code == 1


def test_conversation_orders_replies_and_tools(tmp_path):
    spans = tmp_path / "s.jsonl"
    _write_conversation(spans)
    lines = runner.invoke(telemetry_group, ["conversation", str(spans)]).output.splitlines()
    assert [ln.split()[2] for ln in lines] == ["LLM", "TOOL", "TOOL", "LLM"]
    only_llm = runner.invoke(telemetry_group, ["conversation", str(spans), "--no-tools"]).output.splitlines()
    assert len(only_llm) == 2


def test_calls_lists_named_tool_only(tmp_path):
    spans = tmp_path / "s.jsonl"
    _write_conversation(spans)
    out = runner.invoke(telemetry_group, ["calls", str(spans), "--tool", "commit", "--json"]).output
    found = json.loads(out)
    assert [(c["agent"], c["tool"]) for c in found] == [("lead", "commit")]
    miss = runner.invoke(telemetry_group, ["calls", str(spans), "--tool", "nothing"])
    assert "no matching tool call" in miss.output
