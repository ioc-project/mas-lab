#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The one ClickHouse HTTP client: every ClickHouse query that sends credentials goes through :func:`query`.

A connection is a mapping with ``host``, ``port``, ``user``, ``database``, and optionally ``uri`` (a base URL
over host and port; ``https://...`` for TLS). No redirect is followed, a loopback server is reached without
the environment's proxy, and a failure names the status or the error class, never the server's answer
(it may repeat the credentials).
"""

from __future__ import annotations

import base64
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from mas.library.telemetry.exceptions import ClickHouseQueryError
from mas.runtime.secrets import check_plain_http
from mas.runtime.secrets.transport import opener_for
from mas.runtime.secrets.url import origin_text

HOST_TEXT = "a host name or address only (no user, password, scheme, path or query)"
_AUTH_HINT = "store the password with: mas-ctl secrets set clickhouse"


def host_is_plain(host: str) -> bool:
    """True when *host* is a host name or address: no userinfo, scheme, path, query or space."""
    return bool(host) and not any(ch in "@/\\?#" or ch.isspace() for ch in host)


def clickhouse_url(conn: Mapping[str, Any]) -> str:
    """The base URL of *conn*: its ``uri``, else ``http://host:port``."""
    if conn.get("uri"):
        return str(conn["uri"]).rstrip("/")
    host, port = str(conn["host"]), int(conn["port"])
    if not host_is_plain(host):
        raise ValueError(f"the ClickHouse host must be {HOST_TEXT}")
    return f"http://{f'[{host}]' if ':' in host else host}:{port}"


def query(conn: Mapping[str, Any], sql: str, *, timeout: float = 15, password: str | None = None) -> str:
    """Run *sql* on the ClickHouse server of *conn* and return the response body.

    A *password* goes only over https or to a loopback host
    (:class:`~mas.runtime.secrets.transport.PlainHttpRefused` otherwise, before anything is sent). Raises
    :class:`ClickHouseQueryError` on an HTTP or connection failure.
    """
    url = clickhouse_url(conn)
    if password:
        check_plain_http(url, what="ClickHouse credentials")
    params = {"query": sql, **({"database": conn["database"]} if conn.get("database") else {})}
    request = urllib.request.Request(f"{url}/?{urllib.parse.urlencode(params)}")
    if conn.get("user") or password:
        creds = base64.b64encode(f"{conn.get('user') or ''}:{password or ''}".encode()).decode()
        request.add_header("Authorization", f"Basic {creds}")
    shown = origin_text(url)
    try:
        with opener_for(url).open(request, timeout=timeout) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        hint = _AUTH_HINT if exc.code in (401, 403, 516) else ""
        raise ClickHouseQueryError(f"query at {shown}", exc, hint=hint) from None
    except OSError as exc:
        reason = getattr(exc, "reason", exc)
        error = reason if isinstance(reason, BaseException) else exc
        raise ClickHouseQueryError(f"connection to {shown}", error) from None


SPAN_KEYS = {
    "session": "session_id",
    "run": "SpanAttributes['mas.run.id']",
    "trace": "TraceId",
    "session_attribute": "SpanAttributes['mas.session.id']",
}


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def fetch_spans(
    conn: Mapping[str, Any],
    key: str,
    *,
    by: str = "session",
    app_name: str | None = None,
    table: str = "otel_traces",
    password: str | None = None,
) -> list[dict[str, Any]]:
    """The spans of one session, run or trace (``by``, a :data:`SPAN_KEYS` name) as ``otel_sdk_spans`` dicts."""
    if by == "trace":
        key = key.lower().removeprefix("0x").zfill(32)
    where = f"{SPAN_KEYS[by]} = {_sql_text(key)}" + (f" AND ServiceName = {_sql_text(app_name)}" if app_name else "")
    sql = (
        "SELECT toUnixTimestamp64Nano(Timestamp) AS StartNs, Duration, TraceId, SpanId, ParentSpanId, SpanName, "
        "SpanKind, ServiceName, ResourceAttributes, SpanAttributes, StatusCode "
        f"FROM {conn.get('database') or 'default'}.{table} WHERE {where} ORDER BY StartNs FORMAT JSONEachRow"
    )
    body = query(conn, sql, timeout=60, password=password)
    return [row_to_sdk_span(json.loads(line)) for line in body.splitlines() if line.strip()]


def _ns_to_isoformat(ns: int) -> str:
    """Unix nanoseconds as an ISO-8601 UTC string (microsecond precision)."""
    moment = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(microseconds=ns // 1000)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def row_to_sdk_span(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ClickHouse ``otel_traces`` row (with ``StartNs``) in the ``otel_sdk_spans.jsonl`` format."""
    start_ns = int(row["StartNs"])
    code = str(row.get("StatusCode", "")).upper()
    resource_attrs = dict(row.get("ResourceAttributes") or {})
    resource_attrs.setdefault("service.name", row.get("ServiceName", ""))
    return {
        "name": row["SpanName"],
        "context": {
            "trace_id": "0x" + row["TraceId"].lower(),
            "span_id": "0x" + row["SpanId"].lower(),
            "trace_state": "[]",
        },
        "kind": "SpanKind.INTERNAL",
        "parent_id": "0x" + row["ParentSpanId"].lower() if row.get("ParentSpanId") else None,
        "start_time": _ns_to_isoformat(start_ns),
        "end_time": _ns_to_isoformat(start_ns + int(row.get("Duration", 0))),
        "status": {"status_code": "OK" if "OK" in code else "ERROR" if "ERROR" in code else "UNSET"},
        "attributes": dict(row.get("SpanAttributes") or {}),
        "events": [],
        "links": [],
        "resource": {"attributes": resource_attrs, "schema_url": ""},
    }


__all__ = ["HOST_TEXT", "SPAN_KEYS", "clickhouse_url", "fetch_spans", "host_is_plain", "query", "row_to_sdk_span"]
