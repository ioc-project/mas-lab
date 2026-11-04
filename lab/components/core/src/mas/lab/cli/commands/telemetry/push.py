#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry push`` command."""
from __future__ import annotations

import json
import sys

import click


@click.command("push")
@click.argument("trace_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--endpoint", "-e",
    default="http://localhost:4318",
    show_default=True,
    help="OTLP HTTP collector endpoint (must accept POST /v1/traces JSON).",
)
@click.option(
    "--service-name", "-s",
    default="mas-runtime",
    show_default=True,
    help="service.name resource attribute.",
)
@click.option(
    "--batch-size",
    default=200,
    show_default=True,
    help="Number of spans per HTTP request.",
)
@click.option(
    "--app-name", "app_name",
    default=None,
    help=(
        "Override service.name resource attribute (takes precedence over --service-name). "
        "Useful to tag a re-exported trace with a distinct app identity, "
        "e.g. 'mas-lab-import-test-001'. Default: no override."
    ),
)
@click.option(
    "--dry-run", is_flag=True,
    help="Convert and print a summary but do not send anything.",
)
@click.option(
    "--json-output", "json_out", is_flag=True,
    help="Print result as JSON (useful for scripting).",
)
def push_cmd(
    trace_file: str,
    endpoint: str,
    service_name: str,
    app_name: str | None,
    batch_size: int,
    dry_run: bool,
    json_out: bool,
) -> None:
    """Convert TRACE_FILE to OTLP and push to an OTel collector.

    TRACE_FILE may be:

    \b
    * MAS events.jsonl  (kind/timestamp/run_id schema — auto-detected)
    * OTel SDK spans.jsonl  (context.trace_id schema — auto-detected)

    Examples:

    \b
        mas-lab telemetry push logs/events.jsonl --endpoint http://otel:4318
        mas-lab telemetry push traces/events.jsonl --dry-run
    """
    from mas.lab.telemetry.otlp_push import push_file

    effective_service_name = app_name if app_name else service_name
    result = push_file(
        path=trace_file,
        endpoint=endpoint,
        service_name=effective_service_name,
        dry_run=dry_run,
        batch_size=batch_size,
    )

    if json_out:
        click.echo(json.dumps(result, indent=2))
    else:
        status_color = {
            "ok": "green",
            "dry-run": "cyan",
            "error": "red",
        }.get(result["status"], "white")

        click.secho(
            f"[{result['status'].upper()}] {result['spans']} spans"
            f" / {result['batches']} batch(es)",
            fg=status_color,
        )
        click.echo(result["detail"])

    if result["status"] == "error":
        sys.exit(1)

