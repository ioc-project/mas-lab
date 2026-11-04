#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone pipeline step functions for the telemetry library.

Pure Python functions — no dependency on ``mas.lab.benchmark.pipeline``.  The
``mas-lab`` CLI / ``mas-lab-graph`` wrap these in adapters so the lab commands are
just shortcuts to the same library code.

Steps accept file paths (and, where natural, :class:`OtelSpanSet` objects) and
return an :class:`~mas.library.telemetry.artifact.OtelSpanSet` or a report dict.

Core steps
----------
run_convert         events.jsonl → OtelSpanSet (needs the ``convert`` extra)
run_verify_spans    otel_sdk_spans.jsonl → structural + SpanSpec report
run_compare_spans   two span files → structural parity report
run_compare_spans_multi  one reference vs many candidate span files

Collector steps
---------------
run_push_otlp       spans/events file → OTLP collector
run_dump_spans      ClickHouse otel_traces → spans JSONL (needs ``clickhouse`` extra)
"""

from mas.library.telemetry.steps.compare_spans import (
    run_compare_spans,
    run_compare_spans_multi,
)
from mas.library.telemetry.steps.convert import run_convert
from mas.library.telemetry.steps.push_otlp import run_dump_spans, run_push_otlp
from mas.library.telemetry.steps.verify_spans import run_verify_spans

__all__ = [
    "run_convert",
    "run_verify_spans",
    "run_compare_spans",
    "run_compare_spans_multi",
    "run_push_otlp",
    "run_dump_spans",
]
