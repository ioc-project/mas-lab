#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry dump`` command."""

from __future__ import annotations

import json

import click

from ._clickhouse import ch_conn_options


@click.command("dump")
@click.argument("session_id")
@click.option(
    "--by",
    "query_by",
    type=click.Choice(["session", "run", "trace"]),
    default="session",
    show_default=True,
    help=(
        "Query key: 'session' → ClickHouse session_id column, "
        "'run' → SpanAttributes['mas.run.id'], "
        "'trace' → TraceId column (hex, with or without 0x prefix)."
    ),
)
@click.option("--app-name", default=None, help="Optional ServiceName filter.")
@click.option(
    "-o",
    "--output",
    "output_path",
    required=True,
    help="Output file path for otel_sdk_spans.jsonl.",
)
@ch_conn_options
def dump_cmd(
    session_id: str,
    query_by: str,
    app_name: str | None,
    output_path: str,
    host: str | None,
    port: int | None,
    user: str | None,
    database: str | None,
    table: str,
    json_out: bool,
) -> None:
    """Dump OTel spans for SESSION_ID from ClickHouse → otel_sdk_spans.jsonl.

    SESSION_ID is matched against the ClickHouse session_id column by default.
    Use --by run to match against SpanAttributes['mas.run.id'] instead.
    Use --by trace to match against the TraceId column directly (hex string,
    with or without the 0x prefix).

    Connection parameters are resolved: CLI flags → $XDG_CONFIG_HOME/mas/connections.yaml
    → CLICKHOUSE_* env vars → built-in defaults (localhost:8123). The password is the
    ``password`` ref of the ``clickhouse`` connection (``{secret: NAME}``); none declared means empty.

    \b
    Examples:
        mas-lab telemetry dump 3e376241-163b-4f78-babf-4b6b054d734f \\
            -o /tmp/session.otel.jsonl
        mas-lab telemetry dump my-session/run1 --by run \\
            -o /tmp/run.otel.jsonl
        mas-lab telemetry dump a53108f7aabbccdd0011223344556677 --by trace \\
            -o /tmp/trace.otel.jsonl
    """
    from pathlib import Path

    from mas.lab.connections import resolve_clickhouse_conn, resolve_clickhouse_password
    from mas.library.telemetry.clickhouse_http import fetch_spans
    from mas.library.telemetry.exceptions import ClickHouseQueryError

    conn = resolve_clickhouse_conn(host=host, port=port, user=user, database=database)
    try:
        spans = fetch_spans(
            conn, session_id, by=query_by, app_name=app_name, table=table, password=resolve_clickhouse_password()
        )
    except (ClickHouseQueryError, ValueError) as exc:
        click.secho(f"[ERROR] {exc}", fg="red", err=True)
        raise SystemExit(1) from None
    if not spans:
        click.secho(f"[WARN] No spans found for {query_by} '{session_id}' in {table}.", fg="yellow")
        if json_out:
            click.echo(json.dumps({"status": "empty", "spans": 0, "path": None}))
        raise SystemExit(1)

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for span in spans:
            fh.write(json.dumps(span, ensure_ascii=False) + "\n")

    result = {
        "status": "ok",
        "spans": len(spans),
        "path": str(out),
        "session_id": session_id,
        "query_by": query_by,
    }

    if json_out:
        click.echo(json.dumps(result, indent=2))
    else:
        click.secho(
            f"[OK] {len(spans)} spans → {out}",
            fg="green",
        )
