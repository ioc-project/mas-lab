#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``JSONLineFileSpanExporter`` — an OTel ``SpanExporter`` writing JSON lines.

Each finished span is written as one JSON object per line (the output of
``ReadableSpan.to_json()``) to a local file.  No Docker, no collector required —
this is what produces the ``otel_sdk_spans.jsonl`` artifact.

Importing this module does not require the OpenTelemetry SDK; ``OTEL_AVAILABLE``
reflects whether it is installed, and :data:`JSONLineFileSpanExporter` is ``None``
when it is not (mirrors the behaviour of the OSS converter).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import (
        SpanExporter,
        SpanExportResult,
    )

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the SDK
    OTEL_AVAILABLE = False


if OTEL_AVAILABLE:

    class JSONLineFileSpanExporter(SpanExporter):  # type: ignore[misc]
        """Writes finished spans as JSON lines to a local file.

        Each line is the output of ``ReadableSpan.to_json()`` — a complete,
        self-contained JSON object including trace_id, span_id, parent_span_id,
        attributes, events, and timing.

        The file is truncated on the first write after construction or after
        :meth:`set_path` is called, preventing duplicate spans from accumulating
        across re-runs.  Supports runtime path switching via :meth:`set_path`.
        """

        def __init__(self, path: str | Path) -> None:
            self._path = Path(path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = threading.Lock()
            self._needs_truncate = True

        def set_path(self, path: str | Path) -> None:
            """Switch output to a new file path (for multi-run scenarios)."""
            with self._lock:
                self._path = Path(path)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._needs_truncate = True

        def reset(self) -> None:
            """Mark the next export to truncate the output file."""
            with self._lock:
                self._needs_truncate = True

        def export(self, spans: "list[ReadableSpan]") -> "SpanExportResult":
            try:
                with self._lock:
                    mode = "w" if self._needs_truncate else "a"
                    with open(self._path, mode, encoding="utf-8") as f:
                        for span in spans:
                            f.write(span.to_json(indent=None) + "\n")
                    self._needs_truncate = False
                return SpanExportResult.SUCCESS
            except Exception:  # pragma: no cover - defensive
                logger.exception("JSONLineFileSpanExporter: write failed")
                return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            pass

    class MultiSpanExporter(SpanExporter):  # type: ignore[misc]
        """Fan-out exporter used when file + OTLP sinks are both configured."""

        def __init__(self, exporters: list) -> None:
            self._exporters = list(exporters)

        def export(self, spans: "list[ReadableSpan]") -> "SpanExportResult":
            results = [exporter.export(spans) for exporter in self._exporters]
            if any(result == SpanExportResult.SUCCESS for result in results):
                return SpanExportResult.SUCCESS
            return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            for exporter in self._exporters:
                exporter.shutdown()

    from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult

    class JSONLineFileLogExporter(LogRecordExporter):  # type: ignore[misc]
        """Writes LogRecords (realtime runtime events) as JSON lines to a local file.

        Each line is ``ReadableLogRecord.to_json()``: ``body`` / ``event_name``,
        ``attributes``, ``timestamp``, ``trace_id`` and ``span_id``. The file is
        truncated on the first write.
        """

        def __init__(self, path: str | Path) -> None:
            self._path = Path(path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = threading.Lock()
            self._needs_truncate = True

        @property
        def path(self) -> Path:
            return self._path

        def export(self, batch) -> "LogRecordExportResult":  # type: ignore[no-untyped-def]
            try:
                with self._lock:
                    mode = "w" if self._needs_truncate else "a"
                    with open(self._path, mode, encoding="utf-8") as f:
                        for record in batch:
                            f.write(record.to_json(indent=None) + "\n")
                    self._needs_truncate = False
                return LogRecordExportResult.SUCCESS
            except Exception:  # pragma: no cover - defensive
                logger.exception("JSONLineFileLogExporter: write failed")
                return LogRecordExportResult.FAILURE

        def force_flush(self, timeout_millis: int = 10_000) -> bool:
            return True  # every batch is written when exported

        def shutdown(self) -> None:
            pass

else:  # pragma: no cover
    JSONLineFileSpanExporter = None  # type: ignore[assignment,misc]
    MultiSpanExporter = None  # type: ignore[assignment,misc]
    JSONLineFileLogExporter = None  # type: ignore[assignment,misc]


__all__ = ["JSONLineFileLogExporter", "JSONLineFileSpanExporter", "MultiSpanExporter", "OTEL_AVAILABLE"]
