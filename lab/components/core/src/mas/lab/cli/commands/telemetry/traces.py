#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry traces`` commands."""
from __future__ import annotations

import json

import click

from ._clickhouse import ch_conn_options as _ch_conn_options
from ._clickhouse import ch_request as _ch_request


@click.group("traces")
def traces_group() -> None:
    """Trace-level queries (ClickHouse OTel)."""


@traces_group.command("list")
@click.option("--app-name", default=None, help="Filter by ServiceName.")
@click.option("--limit", default=20, show_default=True, help="Max rows to return.")
@_ch_conn_options
def list_traces_cmd(
    app_name: str | None,
    limit: int,
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
    database: str | None,
    table: str,
    json_out: bool,
) -> None:
    """List distinct trace IDs in the OTel ClickHouse table.

    Use --app-name to scope to one application.  The TraceId can be passed
    directly to ``mas-lab telemetry dump --by trace``.

    \b
    Examples:
        mas-lab telemetry traces list
        mas-lab telemetry traces list --app-name test-export-prod
        mas-lab telemetry traces list --limit 5 --json-output
    """
    from mas.lab.connections import resolve_clickhouse_conn

    conn = resolve_clickhouse_conn(host=host, port=port, user=user, database=database)
    if password is not None:
        conn["password"] = password

    app_filter = f" WHERE ServiceName = '{app_name}'" if app_name else ""
    db_table = f"{conn['database']}.{table}"

    query = (
        f"SELECT TraceId, ServiceName, count() AS n, max(Timestamp) AS last "
        f"FROM {db_table}{app_filter} "
        f"GROUP BY TraceId, ServiceName ORDER BY last DESC "
        f"LIMIT {limit} FORMAT JSONEachRow"
    )

    body = _ch_request(conn, query)
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]

    if json_out:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        click.secho(f"No traces found in {db_table}.", fg="yellow")
        return

    click.echo(
        f"Found {len(rows)} trace(s) in {conn['host']}:{conn['port']}/{db_table}:"
    )
    for row in rows:
        click.echo(
            f"  [{row['last'][:19]}]  {row['TraceId']}  "
            f"n={row['n']}  app={row['ServiceName']}"
        )

