#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas.lab.telemetry — re-exports from ``mas.library.telemetry``."""

from mas.library.telemetry.collector.otlp import load_events, push_file
from mas.library.telemetry.verification import (
    SpanValidator,
    compare_otel_span_files,
    compare_otel_span_files_multi,
    compare_otel_span_sets,
    verify_otel_file,
    verify_otel_spans,
)

__all__ = [
    "push_file",
    "load_events",
    "SpanValidator",
    "verify_otel_spans",
    "verify_otel_file",
    "compare_otel_span_sets",
    "compare_otel_span_files",
    "compare_otel_span_files_multi",
]
