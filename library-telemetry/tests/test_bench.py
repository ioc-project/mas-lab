#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark-adapter tests — skipped unless the bench framework is installed.

Validates that (a) the core library never requires the bench framework, and
(b) when it IS present, the adapters wrap the library step functions correctly.
"""

from __future__ import annotations

import importlib.util

import pytest


def _has_bench() -> bool:
    try:
        return importlib.util.find_spec("mas.lab.benchmark.pipeline") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


_HAS_BENCH = _has_bench()
requires_bench = pytest.mark.skipif(
    not _HAS_BENCH, reason="bench extra not installed"
)


def test_core_library_does_not_require_bench():
    # Importing the package must never pull in the bench framework.
    import mas.library.telemetry  # noqa: F401

    # bench.py is only imported when the framework resolves entry points.
    assert "mas.library.telemetry.bench" not in __import__("sys").modules or _HAS_BENCH


@requires_bench
def test_adapters_register_and_wrap():
    from mas.lab.benchmark.pipeline import PipelineStep
    from mas.library.telemetry.bench import EventsToOtelStep, VerifyOtelStep

    assert issubclass(EventsToOtelStep, PipelineStep)
    assert issubclass(VerifyOtelStep, PipelineStep)
    # events_to_otel: TEMPORARY suffix, avoids colliding with
    # library-lab's own events_to_otel -- see library.yaml.
    assert EventsToOtelStep.type == "events_to_otel"
    assert VerifyOtelStep.type == "verify_otel"


@requires_bench
def test_steps_discoverable_via_manifest():
    from mas.lab.benchmark.pipeline import list_steps

    # Declared in library.yaml's `plugins:` block — the actual discovery
    # mechanism the bench framework's step registry reads from.
    steps = list_steps()
    assert {"events_to_otel", "verify_otel"} <= steps.keys()


@requires_bench
def test_resolve_run_file_uses_output_dir_when_scope_is_missing(tmp_path):
    """Standalone ``pipeline run -o <run folder>`` has no scope_context."""
    from mas.library.telemetry.bench import _resolve_run_file

    events = tmp_path / "traces" / "events.jsonl"
    events.parent.mkdir()
    events.write_text("{}\n", encoding="utf-8")

    class _RunFolder:
        output_dir = tmp_path
        scope_context = None

    assert _resolve_run_file(_RunFolder(), {}, "events.jsonl") == str(events)

    empty = tmp_path / "empty"
    empty.mkdir()

    class _Empty:
        output_dir = empty
        scope_context = None

    assert _resolve_run_file(_Empty(), {}, "events.jsonl") is None
