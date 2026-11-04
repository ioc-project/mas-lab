#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry verify`` command."""
from __future__ import annotations

import json

import click


@click.command("verify")
@click.argument("spans_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--fail/--no-fail",
    default=False,
    help="Exit with code 1 if any check fails.",
)
@click.option("--json-output", "json_out", is_flag=True, help="Print report as JSON.")
def verify_cmd(
    spans_file: str,
    fail: bool,
    json_out: bool,
) -> None:
    """Validate structure of an otel_sdk_spans.jsonl file.

    Checks performed:

    \b
      1. file_not_empty       — at least one span
      2. required_fields      — every span has name, context.span_id,
                                context.trace_id, start_time, end_time
      3. mas_boundary_present — at least one span with mas.boundary attribute
      4. root_span_present    — at least one span without a parent (session root)
      5. single_trace         — all spans share the same trace_id

    \b
    Examples:
        mas-lab telemetry verify /tmp/session.otel.jsonl
        mas-lab telemetry verify /tmp/session.otel.jsonl --fail --json-output
    """
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict = {}

    # Load spans
    spans = []
    with open(spans_file, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: invalid JSON — {exc}")

    stats["total_spans"] = len(spans)

    # 1. Non-empty
    if not spans:
        errors.append("file_not_empty: no spans found")
    else:
        # 2. Required fields
        required_span_fields = ["name", "start_time", "end_time"]
        required_ctx_fields  = ["span_id", "trace_id"]
        bad_spans = []
        for i, s in enumerate(spans):
            missing = [f for f in required_span_fields if not s.get(f)]
            ctx = s.get("context") or {}
            missing += [f"context.{f}" for f in required_ctx_fields if not ctx.get(f)]
            if missing:
                bad_spans.append(f"span[{i}] ({s.get('name', '?')}): missing {missing}")
        if bad_spans:
            errors.append(f"required_fields: {len(bad_spans)} spans missing fields")
            for b in bad_spans[:5]:
                errors.append(f"  {b}")

        # 3. MAS boundary attribute
        mas_spans = [s for s in spans if s.get("attributes", {}).get("mas.boundary")]
        stats["mas_spans"] = len(mas_spans)
        if not mas_spans:
            warnings.append(
                "mas_boundary_present: no span has mas.boundary attribute "
                "— trace may not be a MAS trace"
            )

        # 4. Root span
        roots = [s for s in spans if not s.get("parent_id")]
        stats["root_spans"] = len(roots)
        if not roots:
            errors.append("root_span_present: no root span found (all spans have parent_id)")

        # 5. Single trace
        trace_ids = {
            (s.get("context") or {}).get("trace_id")
            for s in spans
            if (s.get("context") or {}).get("trace_id")
        }
        stats["trace_ids"] = len(trace_ids)
        if len(trace_ids) > 1:
            warnings.append(
                f"single_trace: {len(trace_ids)} distinct trace_ids found "
                f"— dump may contain multiple runs"
            )

        # Collect agents
        agents = sorted({
            s.get("attributes", {}).get("mas.agent.id", "")
            for s in spans
            if s.get("attributes", {}).get("mas.agent.id")
        })
        stats["agents"] = agents

    ok = len(errors) == 0
    report = {
        "file":     spans_file,
        "ok":       ok,
        "errors":   errors,
        "warnings": warnings,
        "stats":    stats,
    }

    if json_out:
        click.echo(json.dumps(report, indent=2))
    else:
        status_color = "green" if ok else "red"
        status_label = "OK" if ok else "FAILED"
        click.secho(f"[{status_label}] {spans_file}", fg=status_color)
        click.echo(f"  spans       : {stats.get('total_spans', 0)}")
        if "mas_spans" in stats:
            click.echo(f"  mas spans   : {stats['mas_spans']}")
        if "agents" in stats:
            click.echo(f"  agents      : {', '.join(stats['agents']) or '—'}")
        if "trace_ids" in stats:
            click.echo(f"  trace ids   : {stats['trace_ids']}")
        if errors:
            click.secho("  Errors:", fg="red")
            for e in errors:
                click.secho(f"    ✗ {e}", fg="red")
        if warnings:
            click.secho("  Warnings:", fg="yellow")
            for w in warnings:
                click.secho(f"    ⚠ {w}", fg="yellow")

    if fail and not ok:
        raise SystemExit(1)
