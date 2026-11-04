#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry apps`` commands."""
from __future__ import annotations

import json

import click


@click.group("apps")
def apps_group() -> None:
    """Application-level queries (ClickHouse OTel)."""


@apps_group.command("list")
@click.option("--host", default=None, help="ClickHouse host (overrides config/env).")
@click.option("--port", type=int, default=None, help="ClickHouse HTTP port (default 8123).")
@click.option("--user", default=None, help="ClickHouse user (overrides config/env).")
@click.option("--password", default=None, help="ClickHouse password (overrides config/env).")
@click.option("--database", default=None, help="ClickHouse database (overrides config/env).")
@click.option("--table", default="otel_traces", show_default=True, help="OTel traces table name.")
@click.option("--start-service", is_flag=True, help="Start the 'clickhouse' service via mas-lab before querying.")
@click.option("--json-output", "json_out", is_flag=True, help="Print result as JSON list.")
def list_apps_cmd(
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
    database: str | None,
    table: str,
    start_service: bool,
    json_out: bool,
) -> None:
    """List ServiceName × application_id pairs from the OTel ClickHouse table.

    Groups spans by ``(ServiceName, application_id)`` and shows counts.
    ``application_id`` is a custom column set by the MAS SDK (distinct from
    ``ServiceName``).  Rows where ``application_id`` is empty or NULL are
    included and displayed as ``(empty)``.

    Connection parameters are resolved: CLI flags → $XDG_CONFIG_HOME/mas/connections.yaml
    → CLICKHOUSE_* env vars → built-in defaults (localhost:8123).

    \b
    Examples:
        mas-lab telemetry apps list
        mas-lab telemetry apps list --user admin --password admin
        mas-lab telemetry apps list --start-service --json-output
    """
    import base64
    import urllib.error
    import urllib.parse
    import urllib.request

    from mas.lab.connections import ensure_service_running, resolve_clickhouse_conn

    if start_service:
        click.echo("Starting ClickHouse service…")
        try:
            ensure_service_running("clickhouse")
            click.echo("ClickHouse service is up.")
        except RuntimeError as exc:
            click.secho(f"[WARNING] Could not start service: {exc}", fg="yellow", err=True)

    conn = resolve_clickhouse_conn(host=host, port=port, user=user, database=database)
    if password is not None:
        conn["password"] = password

    query = (
        f"SELECT ServiceName, application_id, count() AS n "
        f"FROM {conn['database']}.{table} "
        f"GROUP BY ServiceName, application_id "
        f"ORDER BY ServiceName, application_id "
        f"FORMAT TSV"
    )
    params = urllib.parse.urlencode({"query": query})
    url = f"http://{conn['host']}:{conn['port']}/?{params}"

    req = urllib.request.Request(url)
    if conn["user"] or conn["password"]:
        creds = base64.b64encode(f"{conn['user']}:{conn['password']}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode().strip()
    except urllib.error.URLError as exc:
        click.secho(
            f"[ERROR] Could not connect to ClickHouse at "
            f"{conn['host']}:{conn['port']}: {exc}",
            fg="red",
        )
        click.echo("Tip: run with --start-service, or check your connection config.")
        raise SystemExit(1)  # noqa: B904

    rows = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            svc = parts[0]
            raw_app = parts[1]
            app_id = None if raw_app in ("", r"\N") else raw_app
            try:
                n = int(parts[2])
            except ValueError:
                n = 0
            rows.append({"service_name": svc, "application_id": app_id, "count": n})

    if json_out:
        click.echo(json.dumps(rows, indent=2))
    else:
        if rows:
            svc_w   = max(len("SERVICE_NAME"),   max(len(r["service_name"]) for r in rows))
            app_w   = max(len("APPLICATION_ID"), max(len(r["application_id"] or "(empty)") for r in rows))
            count_w = max(len("COUNT"),           max(len(str(r["count"])) for r in rows))
            click.echo(
                f"  {'SERVICE_NAME':<{svc_w}}  {'APPLICATION_ID':<{app_w}}  {'COUNT':>{count_w}}"
            )
            click.echo(f"  {'-'*svc_w}  {'-'*app_w}  {'-'*count_w}")
            for r in rows:
                app_id_disp = r["application_id"] if r["application_id"] is not None else "(empty)"
                click.echo(f"  {r['service_name']:<{svc_w}}  {app_id_disp:<{app_w}}  {r['count']:>{count_w}}")
        else:
            click.secho(
                f"No rows found in {table} — table may be empty or not yet created.",
                fg="yellow",
            )

