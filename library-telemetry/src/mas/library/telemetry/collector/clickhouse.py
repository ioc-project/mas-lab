#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dump OTel spans back out of a ClickHouse ``otel_traces`` table.

This is the *read-back* side of collector serialization — the analogue of
``library-kg``'s ``fetch_kg_from_neo4j``.  After spans have been pushed to a
collector that persists to ClickHouse, :func:`dump_spans` fetches them again for
a given session (or trace) so they can be re-verified or folded into a KG.

Requires the ``clickhouse`` extra::

    this library's ``clickhouse`` extra
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.library.telemetry.exceptions import ClickHouseUnavailableError


def _get_client(
    *,
    host: Optional[str],
    port: Optional[int],
    user: Optional[str],
    database: Optional[str],
    password_env: str,
):
    try:
        import clickhouse_connect  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ClickHouseUnavailableError() from exc

    return clickhouse_connect.get_client(
        host=host or os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=port or int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=user or os.environ.get("CLICKHOUSE_USER", "admin"),
        password=os.environ.get(password_env, ""),
        database=database or os.environ.get("CLICKHOUSE_DATABASE", "default"),
    )


def dump_spans(
    session_id: str,
    *,
    query_by: str = "session",
    output_path: Optional[str | Path] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    database: Optional[str] = None,
    table: str = "otel_traces",
    app_name: Optional[str] = None,
    password_env: str = "CLICKHOUSE_PASSWORD",
) -> Dict[str, Any]:
    """Dump spans for *session_id* from ClickHouse to a JSONL file.

    Parameters
    ----------
    session_id:
        The ingested ClickHouse ``session_id`` column value
        (``query_by="session"``) or a hex ``TraceId`` (``query_by="trace"``).
    query_by:
        ``"session"`` or ``"trace"``.
    output_path:
        Destination JSONL path.  Defaults to ``/tmp/<session_id>.otel.jsonl``.
    app_name:
        Optional ``ServiceName`` filter.

    Returns
    -------
    dict
        ``{"session_id", "query_by", "spans": int, "output": str}``.
    """
    _database = database or os.environ.get("CLICKHOUSE_DATABASE", "default")
    out = Path(output_path) if output_path else Path(f"/tmp/{session_id}.otel.jsonl")

    if query_by == "session":
        where = f"session_id = {{{session_id!r}}}"
    else:
        where = f"TraceId = {{{session_id!r}}}"
    if app_name:
        where += f" AND ServiceName = {{{app_name!r}}}"

    query = f"SELECT * FROM {_database}.{table} WHERE {where} ORDER BY Timestamp ASC FORMAT JSONEachRow"

    client = _get_client(host=host, port=port, user=user, database=database, password_env=password_env)
    rows = client.query(query)

    out.parent.mkdir(parents=True, exist_ok=True)
    col_names = rows.column_names
    span_count = 0
    with out.open("w", encoding="utf-8") as fh:
        for row in rows.result_rows:
            fh.write(json.dumps(dict(zip(col_names, row, strict=True)), default=str) + "\n")
            span_count += 1

    return {
        "session_id": session_id,
        "query_by": query_by,
        "spans": span_count,
        "output": str(out),
    }


def list_apps(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    database: Optional[str] = None,
    table: str = "otel_traces",
    password_env: str = "CLICKHOUSE_PASSWORD",
) -> List[Dict[str, Any]]:
    """List apps (``ServiceName``) present in the ClickHouse ``otel_traces`` table."""
    _database = database or os.environ.get("CLICKHOUSE_DATABASE", "default")
    query = (
        f"SELECT ServiceName, count() AS spans, "
        f"uniq(session_id) AS sessions "
        f"FROM {_database}.{table} "
        f"GROUP BY ServiceName ORDER BY spans DESC FORMAT JSONEachRow"
    )
    client = _get_client(host=host, port=port, user=user, database=database, password_env=password_env)
    rows = client.query(query)
    col_names = rows.column_names
    return [dict(zip(col_names, r, strict=True)) for r in rows.result_rows]


__all__ = ["dump_spans", "list_apps"]
