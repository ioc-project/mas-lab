#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OTel codecs — encoder/decoder pairs for ``otel_traces`` and ``events`` artifact kinds.

Registered codecs
-----------------
* ``("otel_traces", "otlp")``      — Push spans to an OTLP HTTP endpoint (encode only).
* ``("otel_traces", "clickhouse")``— Query spans from ClickHouse (decode only).
* ``("otel_traces", "filesystem")``— Write/read spans as JSONL on disk (both).
* ``("events",      "filesystem")``— Write/read raw events.jsonl on disk (both).

These codecs are registered declaratively via ``library-lab/library.yaml``'s
``types:``/``plugins:`` block through the runtime plugin
registry (see :mod:`mas.runtime.registry.bootstrap` and
``runtime/docs/plugin-registry-manifests.md``) — the same generic
manifest/fixpoint mechanism used for every other plugin category. There is
no bench-local codec registry; :func:`mas.lab.benchmark.codecs.get_codec`
is a thin lookup over ``mas.runtime.registry.get_registry()``. The classes
below are plain :class:`Codec` subclasses with no registration side effect
of their own — do not add a decorator-based registration call here, it
would just duplicate the manifest entry above.

Usage in a pipeline YAML::

    # Push traces to OTLP collector
    - type: serialize
      name: push_otel
      depends_on: [export_otel]
      config:
        artifact_kind: otel_traces
        source_step: export_otel
        source_key: spans
        store:
          type: otlp
          uri: http://localhost:4318

    # Read traces back from ClickHouse
    - type: deserialize
      name: load_traces
      config:
        artifact_kind: otel_traces
        store:
          type: clickhouse
          host: localhost
          port: 8123
          user: admin
          password_env: CLICKHOUSE_PASSWORD
          database: default
        opts:
          session_id: "{session_id}"
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mas.lab.benchmark.codecs.base import Codec
from mas.lab.infra.datastore import DatastoreSpec
from mas.runtime import hostenv

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ClickHouse row → otel_sdk_spans format
# (canonical ClickHouse row → otel_sdk_spans conversion)
# ---------------------------------------------------------------------------


def _ns_to_isoformat(ns: int) -> str:
    """Convert Unix nanoseconds to ISO-8601 string (microsecond precision)."""
    import datetime

    us = ns // 1000
    dt = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=us)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ch_status_to_sdk(code: str) -> str:
    """Map ClickHouse OTel StatusCode string to SDK status_code string."""
    code = code.upper()
    if "OK" in code:
        return "OK"
    if "ERROR" in code:
        return "ERROR"
    return "UNSET"


def ch_row_to_sdk_span(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one ClickHouse otel_traces row to otel_sdk_spans.jsonl format.

    Public so downstream code (normalize_otel etc.) can import it from here.
    """
    start_ns = int(row["StartNs"])
    duration = int(row.get("Duration", 0))
    end_ns = start_ns + duration

    trace_id = "0x" + row["TraceId"].lower()
    span_id = "0x" + row["SpanId"].lower()
    parent_id = "0x" + row["ParentSpanId"].lower() if row.get("ParentSpanId") else None

    attrs = dict(row.get("SpanAttributes") or {})
    resource_attrs = dict(row.get("ResourceAttributes") or {})
    resource_attrs.setdefault("service.name", row.get("ServiceName", ""))

    return {
        "name": row["SpanName"],
        "context": {
            "trace_id": trace_id,
            "span_id": span_id,
            "trace_state": "[]",
        },
        "kind": "SpanKind.INTERNAL",
        "parent_id": parent_id,
        "start_time": _ns_to_isoformat(start_ns),
        "end_time": _ns_to_isoformat(end_ns),
        "status": {
            "status_code": _ch_status_to_sdk(row.get("StatusCode", "")),
        },
        "attributes": attrs,
        "events": [],
        "links": [],
        "resource": {
            "attributes": resource_attrs,
            "schema_url": "",
        },
    }


# ---------------------------------------------------------------------------
# ClickHouse connection helpers
# ---------------------------------------------------------------------------


def _resolve_ch_conn(store: DatastoreSpec) -> dict[str, Any]:
    """Resolve ClickHouse connection from DatastoreSpec + connections.yaml fallback.

    Resolution order:
    1. Fields set on *store* (host, port, user, database)
    2. ``store.password_env`` → env var value
    3. ``resolve_clickhouse_conn()`` for any missing fields (connections.yaml → env → defaults)
    """
    from mas.lab.connections import resolve_clickhouse_conn

    password = ""
    if store.password_env:
        password = hostenv.get(store.password_env, "")

    conn = resolve_clickhouse_conn(
        host=store.host or None,
        port=store.port or None,
        user=store.user or None,
        database=store.database or None,
    )
    if password:
        conn["password"] = password
    # URI override: parse host/port from store.uri if set
    if store.uri:
        conn["uri"] = store.uri
    return conn


def _clickhouse_url(conn: dict[str, Any]) -> str:
    """Build the ClickHouse HTTP base URL from a resolved connection dict."""
    if conn.get("uri"):
        return conn["uri"].rstrip("/")
    return f"http://{conn['host']}:{conn['port']}"


def _clickhouse_query(
    store: DatastoreSpec,
    query: str,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Execute a ClickHouse query and return raw rows as dicts (FORMAT JSONEachRow)."""
    conn = _resolve_ch_conn(store)
    base_url = _clickhouse_url(conn)
    database = conn.get("database", "default")

    params = urllib.parse.urlencode({"database": database, "query": query})
    url = f"{base_url}/?{params}"
    req = urllib.request.Request(url)
    user = conn.get("user", "")
    password = conn.get("password", "")
    if user or password:
        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 403:
            raise RuntimeError(
                f"ClickHouse authentication failed at {base_url} (user={user!r}). "
                f"Check store.password_env or set the env var."
            ) from exc
        raise RuntimeError(f"ClickHouse HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to ClickHouse at {base_url}: {exc}") from exc

    return [json.loads(line) for line in body.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# ("otel_traces", "otlp")  — encode only
# ---------------------------------------------------------------------------


class OtlpOtelCodec(Codec):
    """Push OTel spans to an OTLP HTTP endpoint (encode only).

    The ``store`` spec must have ``type: otlp`` and ``uri`` (or ``host``/``port``)
    pointing to the OTLP collector HTTP port (typically 4318).

    ``artifact`` must be either:
    - a list of event dicts (``events.jsonl`` format) — converted to OTLP spans
    - a ``Path`` to an ``events.jsonl`` file — read and converted

    Extra ``opts``::

        service_name  str   OTel service.name resource attribute (default: "mas-runtime")
        batch_size    int   Spans per HTTP request (default: 200)
        dry_run       bool  Convert but do not push (default: False)
    """

    artifact_kind: str = "otel_traces"
    store_type: str = "otlp"

    def encode(self, artifact: Any, **opts: Any) -> None:
        from mas.lab.telemetry.otlp_push import load_events, push_file

        service_name: str = opts.get("service_name", "mas-runtime")
        batch_size: int = int(opts.get("batch_size", 200))
        dry_run: bool = bool(opts.get("dry_run", False))

        # Determine OTLP endpoint
        if self.store.uri:
            endpoint = self.store.uri.rstrip("/")
        else:
            host = self.store.host or "localhost"
            port = self.store.port or 4318
            endpoint = f"http://{host}:{port}"

        if isinstance(artifact, (str, Path)):
            events = load_events(artifact)
        elif isinstance(artifact, list):
            events = artifact
        else:
            raise TypeError(f"OtlpOtelCodec.encode: expected list[dict] or Path, got {type(artifact)!r}")

        if dry_run:
            logger.info("[dry-run] Would push %d events to %s", len(events), endpoint)
            return

        logger.info("OtlpOtelCodec: pushing %d events → %s", len(events), endpoint)
        push_file(
            events=events,
            endpoint=endpoint,
            service_name=service_name,
            batch_size=batch_size,
        )

    def decode(self, **_: Any) -> Any:
        raise NotImplementedError(
            "OtlpOtelCodec supports encode only. Use ClickHouseOtelCodec (store_type='clickhouse') to read OTel spans."
        )


# ---------------------------------------------------------------------------
# ("otel_traces", "clickhouse")  — decode only
# ---------------------------------------------------------------------------


class ClickHouseOtelCodec(Codec):
    """Query OTel spans from ClickHouse (decode only).

    The ``store`` spec must have ``type: clickhouse`` with host/port/user/
    password_env/database.

    Extra ``opts`` for :meth:`decode`::

        session_id  str   Filter by ``SpanAttributes['mas.session.id']``
        run_id      str   Filter by ``SpanAttributes['mas.run.id']``
        trace_id    str   Filter by ``TraceId`` column (hex, with/without 0x)
        by          str   'session' | 'run' | 'trace' — alternate selector
        app_name    str   Filter by ``ServiceName``
        table       str   ClickHouse table name (default: "otel_traces")
    """

    artifact_kind: str = "otel_traces"
    store_type: str = "clickhouse"

    def encode(self, artifact: Any, **_: Any) -> None:
        raise NotImplementedError(
            "ClickHouseOtelCodec supports decode only. Use OtlpOtelCodec (store_type='otlp') to push OTel spans."
        )

    def decode(self, **opts: Any) -> list[dict[str, Any]]:
        session_id: str | None = opts.get("session_id")
        run_id: str | None = opts.get("run_id")
        trace_id: str | None = opts.get("trace_id")
        by: str = opts.get("by", "session")
        app_name: str | None = opts.get("app_name")
        table: str = opts.get("table", "otel_traces")

        # Normalise: --by trace or trace_id kwarg
        if by == "trace" and not trace_id:
            trace_id = session_id or run_id
            session_id = run_id = None

        if not session_id and not run_id and not trace_id:
            raise ValueError("ClickHouseOtelCodec.decode: provide session_id, run_id, or trace_id in opts.")

        database = self.store.database or "default"
        db_table = f"{database}.{table}"
        app_filter = f" AND ServiceName = '{app_name}'" if app_name else ""

        if trace_id:
            hex_id = trace_id.lower().replace("0x", "").zfill(32)
            safe_hex = hex_id.replace("'", "\\'")
            where_clause = f"TraceId = '{safe_hex}'{app_filter}"
            log_key = f"trace_id={trace_id}"
        else:
            effective_id = session_id or run_id
            attr_key = "mas.session.id" if session_id else "mas.run.id"
            safe_id = (effective_id or "").replace("'", "\\'")
            where_clause = f"SpanAttributes['{attr_key}'] = '{safe_id}'{app_filter}"
            log_key = f"session_id={session_id} run_id={run_id}"

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

        logger.info(
            "ClickHouseOtelCodec.decode: %s table=%s",
            log_key,
            db_table,
        )
        raw_rows = _clickhouse_query(self.store, query)
        return [ch_row_to_sdk_span(row) for row in raw_rows]


# ---------------------------------------------------------------------------
# ("otel_traces", "filesystem")  — encode + decode
# ---------------------------------------------------------------------------


class FilesystemOtelCodec(Codec):
    """Write/read OTel spans as JSONL on the local filesystem.

    The ``store`` spec must have ``type: filesystem`` and ``path`` pointing to
    a directory.  Individual files are named via ``opts.filename``.

    Extra ``opts``::

        filename  str   File name within store.path (default: "otel_spans.jsonl")
    """

    artifact_kind: str = "otel_traces"
    store_type: str = "filesystem"

    def encode(self, artifact: Any, **opts: Any) -> None:
        filename: str = opts.get("filename", "otel_spans.jsonl")
        base = Path(self.store.path)
        base.mkdir(parents=True, exist_ok=True)
        out = base / filename

        if isinstance(artifact, (str, Path)):
            import shutil

            shutil.copy2(artifact, out)
            logger.info("FilesystemOtelCodec: copied %s → %s", artifact, out)
        elif isinstance(artifact, list):
            with open(out, "w") as fh:
                for item in artifact:
                    fh.write(json.dumps(item) + "\n")
            logger.info("FilesystemOtelCodec: wrote %d spans → %s", len(artifact), out)
        else:
            raise TypeError(f"FilesystemOtelCodec.encode: expected list[dict] or Path, got {type(artifact)!r}")

    def decode(self, **opts: Any) -> list[dict[str, Any]]:
        filename: str = opts.get("filename", "otel_spans.jsonl")
        path = Path(self.store.path) / filename
        if not path.exists():
            raise FileNotFoundError(f"FilesystemOtelCodec: file not found: {path}")
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# ("events", "filesystem")  — encode + decode
# ---------------------------------------------------------------------------


class FilesystemEventsCodec(Codec):
    """Write/read raw events (``events.jsonl`` format) on the local filesystem.

    Extra ``opts``::

        filename  str   File name within store.path (default: "events.jsonl")
    """

    artifact_kind: str = "events"
    store_type: str = "filesystem"

    def encode(self, artifact: Any, **opts: Any) -> None:
        filename: str = opts.get("filename", "events.jsonl")
        base = Path(self.store.path)
        base.mkdir(parents=True, exist_ok=True)
        out = base / filename

        if isinstance(artifact, (str, Path)):
            import shutil

            shutil.copy2(artifact, out)
            logger.info("FilesystemEventsCodec: copied %s → %s", artifact, out)
        elif isinstance(artifact, list):
            with open(out, "w") as fh:
                for item in artifact:
                    fh.write(json.dumps(item) + "\n")
            logger.info("FilesystemEventsCodec: wrote %d events → %s", len(artifact), out)
        else:
            raise TypeError(f"FilesystemEventsCodec.encode: expected list[dict] or Path, got {type(artifact)!r}")

    def decode(self, **opts: Any) -> list[dict[str, Any]]:
        filename: str = opts.get("filename", "events.jsonl")
        path = Path(self.store.path) / filename
        if not path.exists():
            raise FileNotFoundError(f"FilesystemEventsCodec: file not found: {path}")
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
