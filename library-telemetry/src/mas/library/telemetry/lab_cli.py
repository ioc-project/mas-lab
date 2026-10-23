#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry`` CLI component — a thin wrapper over the library.

Registered via the ``mas.lab.cli.components`` entry-point group so ``mas-lab
telemetry ...`` (and the legacy ``mas-lab otel`` alias) work as shortcuts to the
same functions the standalone ``mas-telemetry`` CLI and the pipeline steps use.

All commands here just call ``mas.library.telemetry`` functions — no logic lives
in this component. Requires ``click`` (pulled in by the ``[bench]`` extra / the
``mas-lab`` CLI).  Imports are at module top: this module is only imported when
the ``mas-lab`` CLI enumerates its components at startup, so any import problem
surfaces there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from mas.library.telemetry.collector.clickhouse import dump_spans, list_apps
from mas.library.telemetry.conversion.mappings.base import registered_kinds
from mas.library.telemetry import inspection
from mas.library.telemetry.steps.compare_spans import run_compare_spans
from mas.library.telemetry.steps.convert import run_convert
from mas.library.telemetry.steps.push_otlp import run_push_otlp
from mas.library.telemetry.steps.verify_spans import run_verify_spans
from mas.library.telemetry.verification.spanspec import (
    get_attrs,
    get_trace_id,
    load_spans,
)


@click.group("telemetry")
def telemetry_group() -> None:
    """Telemetry utilities: convert, verify, inspect, push, dump OTel spans."""


@telemetry_group.command("convert")
@click.argument("events_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", required=True)
@click.option(
    "--service-name",
    default="",
    help="MAS name when --app-name is unset and events do not carry one.",
)
@click.option(
    "--app-name",
    default="",
    help="MAS name (application_id and service.name). Required unless events already set it.",
)
@click.option(
    "--shift-to-now",
    is_flag=True,
    help="Translate timestamps so the earliest event is now (relative gaps kept).",
)
@click.option(
    "--new-session-id",
    is_flag=True,
    help="Allocate a new session UUID, distinct from the original run.",
)
@click.option(
    "--rewrite-tool-delegation/--no-rewrite-tool-delegation",
    default=True,
    help="Rewrite delegate_to_* tool calls as agent handoffs (default: on).",
)
@click.option(
    "--governance/--no-governance",
    default=False,
    help="Include the governance export layer (default: off).",
)
@click.option(
    "--layer",
    "layers",
    multiple=True,
    type=click.Choice(
        ["structure", "execution", "semantic", "provenance", "governance"],
        case_sensitive=False,
    ),
    help="Enable only these export layers (repeatable). Default: structure, execution, semantic, provenance.",
)
@click.option(
    "--realtime",
    is_flag=True,
    help=(
        "Emit incremental topology.node.*/tool.*/llm.* signal spans as each "
        "event replays, instead of the single end-of-run *.graph span "
        "(realtime replaces it, same as the live otel plugin's realtime mode)."
    ),
)
def _convert(
    events_path: str,
    output: str,
    service_name: str,
    app_name: str,
    shift_to_now: bool,
    new_session_id: bool,
    rewrite_tool_delegation: bool,
    governance: bool,
    layers: tuple[str, ...],
    realtime: bool,
) -> None:
    """events.jsonl → otel_sdk_spans.jsonl."""
    export_layers = None
    if layers:
        chosen = {name.lower() for name in layers}
        export_layers = {
            "structure": "structure" in chosen,
            "execution": "execution" in chosen,
            "semantic": "semantic" in chosen,
            "provenance": "provenance" in chosen,
            "governance": "governance" in chosen,
        }
    elif governance:
        export_layers = {"governance": True}
    ss = run_convert(
        events_path,
        output_dir=Path(output).parent,
        output_filename=Path(output).name,
        service_name=service_name,
        app_name=app_name,
        export_layers=export_layers,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
        rewrite_tool_delegation=rewrite_tool_delegation,
        realtime=realtime,
    )
    click.echo(json.dumps({"spans": ss.span_count, "output": output}, indent=2))


@telemetry_group.command("verify")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--level", default="L3")
@click.option("--spanspec", "spanspec_path", default=None)
@click.option("--fail", "fail_on_error", is_flag=True)
def _verify(
    spans_file: str, level: str, spanspec_path: str | None, fail_on_error: bool
) -> None:
    """Verify a spans file (structural + JSON schema + SpanSpec)."""
    report = run_verify_spans(
        spans_file,
        spanspec_level=level,
        spanspec_path=spanspec_path,
        fail_on_error=fail_on_error,
    )
    click.echo(json.dumps(report["spanspec"]["conformance"], indent=2))
    if fail_on_error and not report["ok"]:
        sys.exit(1)


@telemetry_group.command("inspect")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
def _inspect(spans_file: str) -> None:
    """Quick span-file summary (no validation)."""
    spans = load_spans(Path(spans_file))
    traces = {get_trace_id(s) for s in spans if get_trace_id(s)}
    boundaries: dict = {}
    for s in spans:
        b = get_attrs(s).get("mas.boundary", "<none>")
        boundaries[b] = boundaries.get(b, 0) + 1
    click.echo(
        json.dumps(
            {"spans": len(spans), "traces": len(traces), "boundaries": boundaries},
            indent=2,
        )
    )


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if width <= 0 or len(text) <= width else text[: width - 1] + "…"


@telemetry_group.command("errors")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--fail", is_flag=True, help="Exit 1 when the file holds any ERROR span.")
def _errors(spans_file: str, fail: bool) -> None:
    """Count spans by status: did the runtime report any error?"""
    report = inspection.error_report(load_spans(Path(spans_file)))
    click.echo(f"spans          {report['spans']}")
    click.echo(f"status counts  {report['status_counts']}")
    click.echo(f"ERROR spans    {report['error_spans']}")
    if fail and report["error_spans"]:
        sys.exit(1)


@telemetry_group.command("conversation")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-tools", is_flag=True, help="Show LLM replies only.")
@click.option("--width", default=170, show_default=True, help="Clip each line (0 = no clip).")
def _conversation(spans_file: str, no_tools: bool, width: int) -> None:
    """Print what each agent said and which tools it called, in order."""
    for turn in inspection.conversation(load_spans(Path(spans_file)), with_tools=not no_tools):
        click.echo(f"{turn.index:03d}  {turn.agent:16}  {turn.kind:4}  {_clip(turn.text, width)}")


@telemetry_group.command("calls")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--tool", "tools", multiple=True, required=True, help="Tool name to list (repeatable).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def _calls(spans_file: str, tools: tuple[str, ...], as_json: bool) -> None:
    """List the calls to named tools, with their inputs (e.g. the tool that commits a decision)."""
    found = inspection.tool_calls(load_spans(Path(spans_file)), list(tools))
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    if not found:
        click.echo("no matching tool call")
    for call in found:
        click.echo(f"{call['index']:03d}  {call['agent']:16}  {call['tool']}  {_clip(call['input'], 0)}")


@telemetry_group.command("push")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--endpoint", default=None)
@click.option("--infra", default=None)
@click.option("--app-name", default="")
@click.option("--dry-run", is_flag=True)
@click.option(
    "--shift-to-now",
    is_flag=True,
    help="Translate timestamps so the earliest span is now (relative gaps kept).",
)
@click.option(
    "--new-session-id",
    is_flag=True,
    help="Rewrite session.id to a new UUID so SCOPE treats this as a new session.",
)
def _push(
    spans_file: str,
    endpoint: str | None,
    infra: str | None,
    app_name: str,
    dry_run: bool,
    shift_to_now: bool,
    new_session_id: bool,
) -> None:
    """Serialize spans to an OTLP collector."""
    result = run_push_otlp(
        spans_file,
        endpoint=endpoint,
        infra=infra,
        app_name=app_name,
        dry_run=dry_run,
        shift_to_now=shift_to_now,
        new_session_id=new_session_id,
    )
    click.echo(json.dumps(result, indent=2))


@telemetry_group.command("dump")
@click.argument("session_id")
@click.option(
    "--by", "query_by", type=click.Choice(["session", "trace"]), default="session"
)
@click.option("--output", "-o", default=None)
def _dump(session_id: str, query_by: str, output: str | None) -> None:
    """Dump a session's spans from ClickHouse to JSONL."""
    click.echo(
        json.dumps(
            dump_spans(session_id, query_by=query_by, output_path=output), indent=2
        )
    )


@telemetry_group.command("list-apps")
def _list_apps() -> None:
    """List apps (ServiceName) present in the ClickHouse otel_traces table."""
    click.echo(json.dumps(list_apps(), indent=2, default=str))


@telemetry_group.command("compare")
@click.argument("reference", type=click.Path(exists=True, dir_okay=False))
@click.argument("candidate", type=click.Path(exists=True, dir_okay=False))
@click.option("--strict/--no-strict", default=True)
def _compare(reference: str, candidate: str, strict: bool) -> None:
    """Structural parity of two span files."""
    report = run_compare_spans(reference, candidate, strict=strict)
    click.echo(json.dumps(report["summary"], indent=2))
    if not report.get("passed"):
        sys.exit(1)


@telemetry_group.command("kinds")
def _kinds() -> None:
    """List the native event kinds with a registered span handler."""
    click.echo(json.dumps(registered_kinds(), indent=2))


class TelemetryCliComponent:
    """CLI component registered via the ``mas.lab.cli.components`` entry point."""

    def register(self, app: "click.Group") -> str | None:
        existing = app.commands.get(telemetry_group.name)
        if existing is telemetry_group:
            return None
        # Replace the core ``mas-lab telemetry`` stub's push/verify/dump with
        # this library's OTel-aware versions, but keep any of its commands
        # with no equivalent here (e.g. `show`, `sessions`, `traces` — native
        # trace inspection, nothing to do with OTel) instead of silently
        # dropping them.
        if isinstance(existing, click.Group):
            for name, cmd in existing.commands.items():
                if name not in telemetry_group.commands:
                    telemetry_group.add_command(cmd, name=name)
        app.add_command(telemetry_group)
        if app.commands.get("otel") is not telemetry_group:
            app.add_command(telemetry_group, name="otel")
        return telemetry_group.name


# Backward-compatible alias for the legacy `otel` CLI component name.
OtelCliComponent = TelemetryCliComponent
