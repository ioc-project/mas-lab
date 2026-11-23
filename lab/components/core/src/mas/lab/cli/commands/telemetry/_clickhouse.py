#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared ClickHouse options and query call for the telemetry commands."""

from __future__ import annotations

import click


def check_host_option(_ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Click callback for a ClickHouse ``--host``: refuse anything but a host, never echoing it."""
    from mas.library.telemetry.clickhouse_http import HOST_TEXT, host_is_plain

    if value is not None and not host_is_plain(value):
        raise click.BadParameter(f"{HOST_TEXT}; store the password with: mas-ctl secrets set clickhouse", param=param)
    return value


def ch_conn_options(fn):
    """Decorator: attach standard ClickHouse connection options to a command."""
    for opt in reversed(
        [
            click.option("--host", default=None, callback=check_host_option, help="ClickHouse host."),
            click.option("--port", type=int, default=None, help="ClickHouse HTTP port."),
            click.option("--user", default=None, help="ClickHouse user."),
            click.option("--database", default=None, help="ClickHouse database."),
            click.option("--table", default="otel_traces", show_default=True, help="OTel traces table."),
            click.option("--json-output", "json_out", is_flag=True, help="Print result as JSON."),
        ]
    ):
        fn = opt(fn)
    return fn


def ch_request(conn: dict, query: str, *, timeout: float = 15) -> str:
    """Run a ClickHouse query (:func:`mas.library.telemetry.clickhouse_http.query`) with the stored password.

    A failure prints one line naming the status or the connection problem, never the server's answer, and exits 1.
    """
    from mas.lab.connections import resolve_clickhouse_password
    from mas.library.telemetry.clickhouse_http import query as ch_query
    from mas.library.telemetry.exceptions import ClickHouseQueryError

    try:
        return ch_query(conn, query, timeout=timeout, password=resolve_clickhouse_password())
    except (ClickHouseQueryError, ValueError) as exc:
        click.secho(f"[ERROR] {exc}", fg="red", err=True)
        raise SystemExit(1) from None
