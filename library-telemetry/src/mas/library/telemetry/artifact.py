#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``OtelSpanSet`` — the canonical data container for OTel span data.

The telemetry counterpart of ``library-kg``'s ``KGArtifact``.  An ``OtelSpanSet``
holds a set of OTel SDK spans plus metadata.  It is the primary unit exchanged
between library-telemetry steps.

Serialization
-------------
* **JSONL / disk** — :meth:`OtelSpanSet.save` writes one SDK span per line
  (``otel_sdk_spans.jsonl``); :meth:`OtelSpanSet.from_file` reads it back.
* **OTLP collector** — :meth:`OtelSpanSet.push_to_collector` pushes the spans to
  an OTLP HTTP endpoint (the analogue of ``KGArtifact.push_to_neo4j``).
* **ClickHouse** — :meth:`OtelSpanSet.fetch_from_clickhouse` reconstructs a span
  set from a ClickHouse ``otel_traces`` table.

Construction
------------
* :meth:`OtelSpanSet.from_events` converts a native ``events.jsonl`` file to spans
  via the library converter (requires the ``convert`` extra).

Quick start::

    from mas.library.telemetry import OtelSpanSet

    spans = OtelSpanSet.from_events("run/traces/events.jsonl", app_name="my-app")
    print(spans.span_count, spans.trace_ids())
    spans.save("run/traces/otel_sdk_spans.jsonl")
    report = spans.validate()          # SpanSpec L1–L4 conformance
    spans.push_to_collector(endpoint="http://localhost:4318")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["OtelSpanSet", "stream_span_sets"]


@dataclass
class OtelSpanSet:
    """A set of OTel SDK spans plus metadata.

    Attributes:
        spans:    List of OTel SDK span dicts (``ReadableSpan.to_json()`` shape).
        metadata: Free-form metadata (``run_id``, ``service_name``, ``source``…).
    """

    spans: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_spans(cls, spans: List[Dict[str, Any]], **metadata: Any) -> "OtelSpanSet":
        """Build a span set from an in-memory list of SDK span dicts."""
        return cls(spans=list(spans or []), metadata=dict(metadata))

    @classmethod
    def from_file(cls, path: str | Path) -> "OtelSpanSet":
        """Load an ``OtelSpanSet`` from an ``otel_sdk_spans.jsonl`` file."""
        p = Path(path).expanduser().resolve()
        spans: List[Dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                spans.append(json.loads(line))
        art = cls(spans=spans, metadata={"_source_path": str(p)})
        return art

    @classmethod
    def from_events(
        cls,
        events_path: str | Path,
        *,
        service_name: str = "",
        app_name: str = "",
        export_layers: "Any | None" = None,
        converter_profile: str | None = "observe_sdk",
        extensions: bool | None = None,
        realtime: bool = False,
        replay_speed: float = 0.0,
        shift_to_now: bool = False,
        new_session_id: bool = False,
        rewrite_tool_delegation: bool = True,
    ) -> "OtelSpanSet":
        """Convert a native ``events.jsonl`` file to an ``OtelSpanSet``.

        Requires the ``convert`` extra (``opentelemetry-sdk``).  Runs the offline
        replay converter into a temporary file, then loads the resulting spans.
        """
        import tempfile

        from mas.library.telemetry.conversion.replay import replay_events_file

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            replay_events_file(
                events_path,
                tmp_path,
                service_name=service_name,
                app_name=app_name,
                export_layers=export_layers,
                converter_profile=converter_profile,
                extensions=extensions,
                realtime=realtime,
                replay_speed=replay_speed,
                shift_to_now=shift_to_now,
                new_session_id=new_session_id,
                rewrite_tool_delegation=rewrite_tool_delegation,
            )
            art = cls.from_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        art.metadata = {
            "source": str(events_path),
            "service_name": service_name,
            "app_name": app_name,
        }
        return art

    @classmethod
    def empty(cls) -> "OtelSpanSet":
        return cls()

    # ------------------------------------------------------------------
    # Serialization — JSONL / disk
    # ------------------------------------------------------------------

    def to_jsonl(self) -> str:
        """Serialise to a JSONL string (one span per line)."""
        return "\n".join(json.dumps(s, ensure_ascii=False) for s in self.spans)

    def save(self, path: str | Path) -> Path:
        """Write the span set to an ``otel_sdk_spans.jsonl`` file."""
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_jsonl() + ("\n" if self.spans else ""), encoding="utf-8")
        logger.debug("OtelSpanSet saved: %d spans → %s", self.span_count, p)
        return p

    # ------------------------------------------------------------------
    # Serialization — OTLP collector / ClickHouse
    # ------------------------------------------------------------------

    def push_to_collector(
        self,
        *,
        endpoint: str,
        service_name: str = "",
        app_name: str = "",
        dry_run: bool = False,
        batch_size: int = 200,
    ) -> Dict[str, Any]:
        """Push these spans to an OTLP HTTP collector.

        Delegates to
        :func:`mas.library.telemetry.collector.otlp.push_spans_to_collector`.
        """
        from mas.library.telemetry.collector.otlp import push_spans_to_collector

        return push_spans_to_collector(
            self.spans,
            endpoint,
            service_name=service_name or str(self.metadata.get("service_name") or ""),
            app_name=app_name or str(self.metadata.get("app_name") or ""),
            dry_run=dry_run,
            batch_size=batch_size,
        )

    @classmethod
    def fetch_from_clickhouse(
        cls,
        session_id: str,
        *,
        query_by: str = "session",
        **kwargs: Any,
    ) -> "OtelSpanSet":
        """Fetch a session's spans from ClickHouse (requires ``clickhouse`` extra)."""
        import tempfile

        from mas.library.telemetry.collector.clickhouse import dump_spans

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = dump_spans(
                session_id, query_by=query_by, output_path=tmp_path, **kwargs
            )
            art = cls.from_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        art.metadata = {"source": "clickhouse", **result}
        return art

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def validate(
        self, *, strictness: str = "required", spec_file: Optional[str] = None
    ):
        """Validate these spans against the SpanSpec; return a ``ValidationReport``."""
        from mas.library.telemetry.verification.spanspec import SpanValidator

        return SpanValidator(spec_file).validate(self.spans, strictness=strictness)  # type: ignore[arg-type]

    def verify_structural(self) -> Dict[str, Any]:
        """Run structural span checks; return the report dict."""
        from mas.library.telemetry.verification.structural import verify_otel_spans

        return verify_otel_spans(self.spans)

    def compare_to(
        self, reference: "OtelSpanSet | list", *, strict: bool = True
    ) -> Dict[str, Any]:
        """Structurally compare this span set (candidate) against a *reference*."""
        from mas.library.telemetry.verification.compare import compare_otel_span_sets

        ref = reference.spans if isinstance(reference, OtelSpanSet) else reference
        return compare_otel_span_sets(ref, self.spans, strict=strict)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def span_count(self) -> int:
        return len(self.spans)

    def trace_ids(self) -> List[str]:
        """Distinct trace ids present in the span set."""
        out = {
            (s.get("context") or {}).get("trace_id")
            for s in self.spans
            if (s.get("context") or {}).get("trace_id")
        }
        return sorted(t for t in out if t)

    def span_names(self) -> Dict[str, int]:
        """Count of spans by ``name``."""
        counts: Dict[str, int] = {}
        for s in self.spans:
            counts[str(s.get("name") or "")] = (
                counts.get(str(s.get("name") or ""), 0) + 1
            )
        return counts

    def run_id(self) -> Optional[str]:
        return self.metadata.get("run_id") or self.metadata.get("runId") or None

    def __repr__(self) -> str:
        return f"OtelSpanSet(spans={self.span_count}, traces={len(self.trace_ids())})"

    def __bool__(self) -> bool:
        return bool(self.spans)


def stream_span_sets(paths: Iterable[str | Path]) -> Iterator[OtelSpanSet]:
    """Lazily load a sequence of ``otel_sdk_spans.jsonl`` files as span sets."""
    for p in paths:
        yield OtelSpanSet.from_file(p)
