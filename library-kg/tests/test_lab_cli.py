#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import click
from click.testing import CliRunner


def test_kg_cli_component_registers_group_and_subcommands():
    from mas.library.kg.lab_cli import KgCliComponent

    target = click.Group()
    registered = KgCliComponent().register(target)

    assert registered == "kg"
    assert "kg" in target.commands

    kg = target.commands["kg"]
    assert isinstance(kg, click.Group)
    assert "dump" in kg.commands
    assert "push" in kg.commands
    assert "normalize" in kg.commands


def test_kg_cli_component_is_idempotent():
    from mas.library.kg.lab_cli import KgCliComponent

    target = click.Group()
    component = KgCliComponent()

    assert component.register(target) == "kg"
    assert component.register(target) is None


def test_normalize_supports_otel_flag_and_output_file(tmp_path, monkeypatch):
    from mas.library.kg.artifact import KGArtifact
    from mas.library.kg.lab_cli import kg_group

    spans_path = tmp_path / "spans.json"
    spans_path.write_text(
        '[{"name":"TaskCall","context":{"trace_id":"abc"},"attributes":{"mas.session.id":"sess-1"}}]',
        encoding="utf-8",
    )
    output_path = tmp_path / "norm.jsonld"

    calls: list[dict[str, object]] = []

    def _fake_run_normalize_otel(**kwargs):
        calls.append(kwargs)
        return KGArtifact(nodes=[{"id": "n1"}], edges=[], metadata={"run_id": kwargs["run_id"]})

    monkeypatch.setattr(
        "mas.library.kg.steps.normalize_otel.run_normalize_otel", _fake_run_normalize_otel
    )

    result = CliRunner().invoke(
        kg_group, ["normalize", "--otel", str(spans_path), "-o", str(output_path)]
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert calls[0]["run_id"] == "sess-1"


def test_normalize_supports_otel_jsonl_dump_file(tmp_path, monkeypatch):
    from mas.library.kg.artifact import KGArtifact
    from mas.library.kg.lab_cli import kg_group

    spans_path = tmp_path / "spans.jsonl"
    spans_path.write_text(
        '{"SpanName":"TaskCall","TraceId":"trace-1","SpanAttributes":{"mas.session.id":"sess-jsonl"}}\n'
        '{"SpanName":"TaskCall","TraceId":"trace-1","SpanAttributes":{"mas.session.id":"sess-jsonl"}}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "norm.jsonld"

    calls: list[dict[str, object]] = []

    def _fake_run_normalize_otel(**kwargs):
        calls.append(kwargs)
        return KGArtifact(nodes=[{"id": "n1"}], edges=[], metadata={"run_id": kwargs["run_id"]})

    monkeypatch.setattr(
        "mas.library.kg.steps.normalize_otel.run_normalize_otel", _fake_run_normalize_otel
    )

    result = CliRunner().invoke(
        kg_group, ["normalize", "--otel", str(spans_path), "-o", str(output_path)]
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert calls[0]["run_id"] == "sess-jsonl"


def test_validate_lists_checks_without_kg_path():
    from mas.library.kg.lab_cli import kg_group

    result = CliRunner().invoke(kg_group, ["validate", "--list-checks"])

    assert result.exit_code == 0
    assert '"shacl"' in result.output
    assert '"run_one"' in result.output


def test_validate_supports_check_and_skip_options(tmp_path, monkeypatch):
    from mas.library.kg.lab_cli import kg_group

    kg_path = tmp_path / "kg.jsonld"
    kg_path.write_text('{"nodes": [], "edges": []}', encoding="utf-8")

    calls: list[dict[str, object]] = []

    def _fake_run_validate_kg(**kwargs):
        calls.append(kwargs)
        return {"error_count": 0, "warning_count": 0, "results": []}

    monkeypatch.setattr("mas.library.kg.steps.validate_kg.run_validate_kg", _fake_run_validate_kg)

    result = CliRunner().invoke(
        kg_group,
        ["validate", str(kg_path), "--check", "shacl", "--check", "unknown_node_types"],
    )

    assert result.exit_code == 0
    assert calls[0]["checks"] == ["shacl", "unknown_node_types"]

    calls.clear()
    result = CliRunner().invoke(kg_group, ["validate", str(kg_path), "--skip-check", "shacl"])

    assert result.exit_code == 0
    assert "shacl" not in calls[0]["checks"]


def test_validate_forwards_warning_verbosity(tmp_path, monkeypatch):
    from mas.library.kg.lab_cli import kg_group

    kg_path = tmp_path / "kg.jsonld"
    kg_path.write_text('{"nodes": [], "edges": []}', encoding="utf-8")

    calls: list[dict[str, object]] = []

    def _fake_run_validate_kg(**kwargs):
        calls.append(kwargs)
        return {"error_count": 0, "warning_count": 0, "results": []}

    monkeypatch.setattr("mas.library.kg.steps.validate_kg.run_validate_kg", _fake_run_validate_kg)

    result = CliRunner().invoke(
        kg_group,
        ["validate", str(kg_path), "--warning-verbosity", "full"],
    )

    assert result.exit_code == 0
    assert calls[0]["warning_verbosity"] == "full"


def test_validate_help_mentions_check_controls():
    from mas.library.kg.lab_cli import kg_group

    result = CliRunner().invoke(kg_group, ["validate", "--help"])

    assert result.exit_code == 0
    assert "--list-checks" in result.output
    assert "--check" in result.output
    assert "--skip-check" in result.output


def test_normalize_forwards_split_session_id_and_app_name(tmp_path, monkeypatch):
    from mas.library.kg.artifact import KGArtifact
    from mas.library.kg.lab_cli import kg_group

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"kind": "execution_start", "run_id": "app_x", "agent_id": "a", "call_id": "c"}\n',
        encoding="utf-8",
    )

    calls: list[dict[str, object]] = []

    def _fake_run_normalize(**kwargs):
        calls.append(kwargs)
        return KGArtifact(nodes=[{"id": "n1"}], edges=[], metadata={"run_id": kwargs["run_id"]})

    monkeypatch.setattr("mas.library.kg.steps.normalize.run_normalize", _fake_run_normalize)

    result = CliRunner().invoke(
        kg_group,
        [
            "normalize",
            str(events_path),
            "--no-split-session-id",
            "--app-name",
            "trip-planner",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["split_session_id"] is False
    assert calls[0]["app_name"] == "trip-planner"


def test_otel_to_kg_forwards_its_arguments(tmp_path, monkeypatch):
    from mas.library.kg.artifact import KGArtifact
    from mas.library.kg.lab_cli import kg_group

    spans_path = tmp_path / "spans.json"
    spans_path.write_text("[]", encoding="utf-8")

    calls: list[dict[str, object]] = []

    def _fake_run_normalize_otel(**kwargs):
        calls.append(kwargs)
        return KGArtifact(nodes=[], edges=[], metadata={"run_id": kwargs["run_id"]})

    monkeypatch.setattr(
        "mas.library.kg.steps.normalize_otel.run_normalize_otel", _fake_run_normalize_otel
    )

    result = CliRunner().invoke(
        kg_group,
        [
            "otel-to-kg",
            str(spans_path),
            "--run-id",
            "r1",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls[0] == {
        "spans_path": str(spans_path),
        "run_id": "r1",
        "output_dir": ".",
        "dry_run": True,
    }


def test_push_forwards_extension_layers(tmp_path, monkeypatch):
    from mas.library.kg.lab_cli import kg_group

    kg_path = tmp_path / "kg.jsonld"
    kg_path.write_text('{"nodes": [], "edges": []}', encoding="utf-8")

    calls: list[dict[str, object]] = []

    def _fake_run_neo4j_push(*args, **kwargs):
        del args
        calls.append(kwargs)
        return {"rows": 0, "nodes": 0, "edges": 0, "uri": "bolt://localhost:7687"}

    monkeypatch.setattr("mas.library.kg.steps.neo4j_push.run_neo4j_push", _fake_run_neo4j_push)

    result = CliRunner().invoke(
        kg_group,
        [
            "push",
            str(kg_path),
            "--enable-extension-layer",
            "experiment",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["extension_layers"] == ["experiment"]
