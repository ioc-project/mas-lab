#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry dump`` command."""
from __future__ import annotations

import json

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

@click.command("dump")
@click.argument("session_id")
@click.option(
    "--by", "query_by",
    type=click.Choice(["session", "run", "trace"]),
    default="session",
    show_default=True,
        help=("Query key: 'session' → ClickHouse session_id column, "
            "'run' → SpanAttributes['mas.run.id'], "
          "'trace' → TraceId column (hex, with or without 0x prefix)."),
)
@click.option("--app-name", default=None, help="Optional ServiceName filter.")
@click.option(
    "-o", "--output", "output_path",
    required=True,
    help="Output file path for otel_sdk_spans.jsonl.",
)
@click.option("--host",     default=None, help="ClickHouse host (overrides config/env).")
@click.option("--port",     type=int, default=None, help="ClickHouse HTTP port.")
@click.option("--user",     default=None, help="ClickHouse user.")
@click.option("--password", default=None, help="ClickHouse password.")
@click.option("--database", default=None, help="ClickHouse database.")
@click.option("--table",    default="otel_traces", show_default=True, help="OTel traces table.")
@click.option("--json-output", "json_out", is_flag=True, help="Print summary as JSON.")
def dump_cmd(
    session_id: str,
    query_by: str,
    app_name: str | None,
    output_path: str,
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
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
    → CLICKHOUSE_* env vars → built-in defaults (localhost:8123).

    \b
    Examples:
        mas-lab telemetry dump 3e376241-163b-4f78-babf-4b6b054d734f \\
            -o /tmp/session.otel.jsonl
        mas-lab telemetry dump my-session/run1 --by run \\
            -o /tmp/run.otel.jsonl
        mas-lab telemetry dump a53108f7aabbccdd0011223344556677 --by trace \\
            -o /tmp/trace.otel.jsonl
    """
    import base64
    import urllib.error
    import urllib.parse
    import urllib.request
    from pathlib import Path

    from mas.lab.connections import resolve_clickhouse_conn

    conn = resolve_clickhouse_conn(host=host, port=port, user=user, database=database)
    if password is not None:
        conn["password"] = password

    db_table = f"{conn['database']}.{table}"
    safe_sid = session_id.replace("'", "\\'")
    app_filter = f" AND ServiceName = '{app_name}'" if app_name else ""

    if query_by == "trace":
        # Normalise: strip leading 0x, pad to 32 hex chars
        trace_hex = safe_sid.lower().lstrip("0x").zfill(32)
        where_clause = f"TraceId = '{trace_hex}'{app_filter}"
        attr_key = "TraceId"
    elif query_by == "session":
        attr_key = "session_id"
        where_clause = f"session_id = '{safe_sid}'{app_filter}"
    else:
        attr_key = "mas.run.id"
        where_clause = f"SpanAttributes['{attr_key}'] = '{safe_sid}'{app_filter}"

    query = (
        f"SELECT "
        f"toUnixTimestamp64Nano(Timestamp) AS StartNs, "
        f"Duration, TraceId, SpanId, ParentSpanId, SpanName, SpanKind, "
        f"ServiceName, ResourceAttributes, SpanAttributes, StatusCode "
        f"FROM {db_table} "
        f"WHERE {where_clause} "
        f"ORDER BY StartNs "
        f"FORMAT JSONEachRow"
    )

    params  = urllib.parse.urlencode({"query": query})
    url     = f"http://{conn['host']}:{conn['port']}/?{params}"
    req     = urllib.request.Request(url)
    if conn.get("user") or conn.get("password"):
        creds = base64.b64encode(
            f"{conn['user']}:{conn['password']}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {creds}")

    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            click.secho(
                f"[ERROR] ClickHouse authentication failed at "
                f"{conn['host']}:{conn['port']} (user={conn['user']!r}).\n"
                f"  → Set CLICKHOUSE_PASSWORD env var or pass --password.\n"
                f"  → For prod: mas-lab services start --service clickhouse --profile prod",
                fg="red",
            )
        else:
            click.secho(
                f"[ERROR] ClickHouse HTTP {exc.code} at "
                f"{conn['host']}:{conn['port']}: {exc}",
                fg="red",
            )
        raise SystemExit(1)  # noqa: B904
    except urllib.error.URLError as exc:
        click.secho(
            f"[ERROR] Could not connect to ClickHouse at "
            f"{conn['host']}:{conn['port']}: {exc}\n"
            f"  → Is the service running? Try: mas-lab services start --service clickhouse",
            fg="red",
        )
        raise SystemExit(1)  # noqa: B904

    rows = [json.loads(line) for line in body.splitlines() if line.strip()]

    if not rows:
        click.secho(
            f"[WARN] No spans found for {attr_key}='{session_id}' in {db_table}.",
            fg="yellow",
        )
        if json_out:
            click.echo(json.dumps({"status": "empty", "spans": 0, "path": None}))
        raise SystemExit(1)

    spans = [_ch_row_to_sdk_span(row) for row in rows]

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for span in spans:
            fh.write(json.dumps(span, ensure_ascii=False) + "\n")

    result = {
        "status":    "ok",
        "spans":     len(spans),
        "path":      str(out),
        "session_id": session_id,
        "query_by":  attr_key,
    }

    if json_out:
        click.echo(json.dumps(result, indent=2))
    else:
        click.secho(
            f"[OK] {len(spans)} spans → {out}",
            fg="green",
        )

