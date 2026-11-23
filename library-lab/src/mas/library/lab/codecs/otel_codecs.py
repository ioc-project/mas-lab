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
          password: {secret: clickhouse}
          database: default
        opts:
          session_id: "{session_id}"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.codecs.base import Codec
from mas.lab.infra.datastore import DatastoreSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ClickHouse connection helpers
# ---------------------------------------------------------------------------


def _resolve_ch_conn(store: DatastoreSpec) -> dict[str, Any]:
    """Resolve ClickHouse connection from DatastoreSpec + connections.yaml fallback.

    Resolution order:
    1. Fields set on *store* (host, port, user, database), ``store.uri`` over host and port
    2. ``resolve_clickhouse_conn()`` for any missing fields (connections.yaml → host → defaults)
    3. ``store.password`` SecretRef (required unless optional); unset means no password
    """
    from mas.lab.connections import resolve_clickhouse_conn, reveal_password

    conn = resolve_clickhouse_conn(
        host=store.host or None,
        port=store.port or None,
        user=store.user or None,
        database=store.database or None,
    )
    if store.uri:
        conn["uri"] = store.uri
    conn["password"] = reveal_password(getattr(store, "password", None))
    return conn


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
        import tempfile

        from mas.library.telemetry.collector.otlp import push_file

        if self.store.uri:
            endpoint = self.store.uri.rstrip("/")
        else:
            endpoint = f"http://{self.store.host or 'localhost'}:{self.store.port or 4318}"
        if not isinstance(artifact, (str, Path, list)):
            raise TypeError(f"OtlpOtelCodec.encode: expected list[dict] or Path, got {type(artifact)!r}")
        with tempfile.TemporaryDirectory() as tmp:
            path = artifact
            if isinstance(artifact, list):
                path = Path(tmp) / "events.jsonl"
                path.write_text("".join(json.dumps(event) + "\n" for event in artifact), encoding="utf-8")
            result = push_file(
                path,
                endpoint,
                service_name=opts.get("service_name", "mas-runtime"),
                dry_run=bool(opts.get("dry_run", False)),
                batch_size=int(opts.get("batch_size", 200)),
            )
        logger.info("OtlpOtelCodec: %s (%s)", result["detail"], result["status"])

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
    password (SecretRef)/database.

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

        from mas.library.telemetry.clickhouse_http import fetch_spans

        keys = (("trace", trace_id), ("session_attribute", session_id), ("run", run_id))
        by, key = next((k, v) for k, v in keys if v)
        logger.info("ClickHouseOtelCodec.decode: %s=%s table=%s", by, key, table)
        conn = _resolve_ch_conn(self.store)
        return fetch_spans(conn, str(key), by=by, app_name=app_name, table=table, password=conn["password"])


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
