#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared ClickHouse helpers for telemetry commands."""
from __future__ import annotations

import click


def _ns_to_isoformat(ns: int) -> str:
    """Convert Unix nanoseconds to ISO-8601 string (microsecond precision)."""
    import datetime
    us = ns // 1000
    dt = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=us)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ch_status_to_sdk(code: str) -> str:
    """Map ClickHouse OTel StatusCode strings to SDK status_code strings."""
    code = code.upper()
    if "OK" in code:
        return "OK"
    if "ERROR" in code:
        return "ERROR"
    return "UNSET"


def _ch_row_to_sdk_span(row: dict) -> dict:
    """Convert one ClickHouse otel_traces row to otel_sdk_spans.jsonl format."""
    start_ns = int(row["StartNs"])
    duration  = int(row.get("Duration", 0))
    end_ns    = start_ns + duration

    trace_id  = "0x" + row["TraceId"].lower()
    span_id   = "0x" + row["SpanId"].lower()
    parent_id = ("0x" + row["ParentSpanId"].lower()) if row.get("ParentSpanId") else None

    attrs = dict(row.get("SpanAttributes") or {})
    resource_attrs = dict(row.get("ResourceAttributes") or {})
    resource_attrs.setdefault("service.name", row.get("ServiceName", ""))

    return {
        "name": row["SpanName"],
        "context": {
            "trace_id": trace_id,
            "span_id":  span_id,
            "trace_state": "[]",
        },
        "kind":      "SpanKind.INTERNAL",
        "parent_id": parent_id,
        "start_time": _ns_to_isoformat(start_ns),
        "end_time":   _ns_to_isoformat(end_ns),
        "status": {
            "status_code": _ch_status_to_sdk(row.get("StatusCode", "")),
        },
        "attributes": attrs,
        "events":  [],
        "links":   [],
        "resource": {
            "attributes": resource_attrs,
            "schema_url": "",
        },
    }


# ---------------------------------------------------------------------------
# Shared ClickHouse connection options (reused by list-sessions / list-traces)
# ---------------------------------------------------------------------------

def _ch_conn_options(fn):
    """Decorator: attach standard ClickHouse connection options to a command."""
    for opt in reversed([
        click.option("--host",     default=None, help="ClickHouse host."),
        click.option("--port",     type=int, default=None, help="ClickHouse HTTP port."),
        click.option("--user",     default=None, help="ClickHouse user."),
        click.option("--password", default=None, help="ClickHouse password."),
        click.option("--database", default=None, help="ClickHouse database."),
        click.option("--table",    default="otel_traces", show_default=True,
                     help="OTel traces table."),
        click.option("--json-output", "json_out", is_flag=True,
                     help="Print result as JSON."),
    ]):
        fn = opt(fn)
    return fn


def _ch_request(conn: dict, query: str) -> str:
    """Run a ClickHouse query and return the raw response body."""
    import base64
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({"query": query})
    url = f"http://{conn['host']}:{conn['port']}/?{params}"
    req = urllib.request.Request(url)
    if conn.get("user") or conn.get("password"):
        creds = base64.b64encode(
            f"{conn['user']}:{conn['password']}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            click.secho(
                f"[ERROR] ClickHouse auth failed at {conn['host']}:{conn['port']}"
                f" (user={conn['user']!r}). Set CLICKHOUSE_PASSWORD or pass --password.",
                fg="red",
            )
        else:
            click.secho(f"[ERROR] ClickHouse HTTP {exc.code}: {exc}", fg="red")
        raise SystemExit(1)  # noqa: B904
    except urllib.error.URLError as exc:
        click.secho(
            f"[ERROR] Cannot connect to ClickHouse at {conn['host']}:{conn['port']}: {exc}",
            fg="red",
        )
        raise SystemExit(1)  # noqa: B904


ch_conn_options = _ch_conn_options
ch_request = _ch_request
