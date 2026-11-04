#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry sessions`` commands."""
from __future__ import annotations

import json

import click

from ._clickhouse import ch_conn_options as _ch_conn_options
from ._clickhouse import ch_request as _ch_request


@click.group("sessions")
def sessions_group() -> None:
    """Session-level queries (ClickHouse OTel)."""


@sessions_group.command("list")
@click.option("--app-name", default=None, help="Filter by ServiceName.")
@click.option(
    "--attr-key", default="mas.session.id", show_default=True,
    help="SpanAttribute key holding the session ID (e.g. 'session.id').",
)
@click.option("--limit", default=20, show_default=True, help="Max rows to return.")
@_ch_conn_options
def list_sessions_cmd(
    app_name: str | None,
    attr_key: str,
    limit: int,
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
    database: str | None,
    table: str,
    json_out: bool,
) -> None:
    """List sessions available in the OTel ClickHouse table.

    Groups spans by the session attribute key (default: mas.session.id) and
    shows span count and last-seen timestamp per session.

    \b
    Examples:
        mas-lab telemetry sessions list
        mas-lab telemetry sessions list --app-name noa-trip-planner-mas --attr-key session.id
        mas-lab telemetry sessions list --limit 5 --json-output
    """
    from mas.lab.connections import resolve_clickhouse_conn

    conn = resolve_clickhouse_conn(host=host, port=port, user=user, database=database)
    if password is not None:
        conn["password"] = password

    app_filter = f" AND ServiceName = '{app_name}'" if app_name else ""
    db_table = f"{conn['database']}.{table}"
    safe_key = attr_key.replace("'", "\\'")

    query = (
        f"SELECT SpanAttributes['{safe_key}'] AS sid, ServiceName, "
        f"count() AS n, max(Timestamp) AS last "
        f"FROM {db_table} "
        f"WHERE SpanAttributes['{safe_key}'] != ''{app_filter} "
        f"GROUP BY sid, ServiceName ORDER BY last DESC "
        f"LIMIT {limit} FORMAT JSONEachRow"
    )

    body = _ch_request(conn, query)
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]

    if json_out:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        click.secho(
            f"No sessions found (attr_key='{attr_key}') in {db_table}.",
            fg="yellow",
        )
        return

    click.echo(
        f"Found {len(rows)} session(s) in {conn['host']}:{conn['port']}"
        f"/{db_table} [attr={attr_key}]:"
    )
    for row in rows:
        click.echo(
            f"  [{row['last'][:19]}]  {row['sid']:<50}  "
            f"n={row['n']}  app={row['ServiceName']}"
        )

