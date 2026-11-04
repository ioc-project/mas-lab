#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Telemetry library — native-events → OTel conversion, verification, serialization.

The telemetry counterpart of ``library-kg/``.  Where that library turns
*OTel spans → KG*, this library turns *native events → OTel spans*, verifies
them against the MAS SpanSpec, and serializes them to an OTLP collector.

Primary public API::

    from mas.library.telemetry import (
        OtelSpanSet, SpanValidator, compare_otel_span_sets,
    )

Artifact
--------
    OtelSpanSet       — primary data container (spans + metadata).
                        Serialises to/from JSONL on disk, an OTLP collector, and
                        ClickHouse.
    stream_span_sets  — lazy-load a sequence of otel_sdk_spans.jsonl files.

Conversion (native events → OTel spans)
--------------------------------------
See :mod:`mas.library.telemetry.conversion` — ``MasOtelConverter``,
``replay_events_file``, and the extensible per-category handler registry.

Verification (span contracts)
-----------------------------
    SpanValidator       — L1–L4 SpanSpec conformance
    verify_otel_spans   — structural checks
    verify_otel_file    — structural + JSON schema + SpanSpec, from a file
    compare_otel_span_sets — structural span parity comparison

Collector serialization
------------------------
See :mod:`mas.library.telemetry.collector` — ``push_file``,
``push_spans_to_collector``, ``dump_spans``.

Pipeline steps
--------------
See :mod:`mas.library.telemetry.steps` for artifact-based step functions.
"""

from __future__ import annotations

import os

os.environ.setdefault("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", "false")

from pathlib import Path

# Artifact — primary data type
from mas.library.telemetry.artifact import OtelSpanSet, stream_span_sets

# Verification
from mas.library.telemetry.verification import (
    SpanValidator,
    ValidationReport,
    Violation,
    compare_otel_span_files,
    compare_otel_span_sets,
    verify_otel_file,
    verify_otel_spans,
)

__all__ = [
    # artifact
    "OtelSpanSet",
    "stream_span_sets",
    # verification
    "SpanValidator",
    "ValidationReport",
    "Violation",
    "verify_otel_spans",
    "verify_otel_file",
    "compare_otel_span_sets",
    "compare_otel_span_files",
]


def package_root() -> Path:
    """Return the package root directory (contains ``library.yaml`` when installed)."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "library.yaml").is_file():
            return parent
    return here.parents[3]
