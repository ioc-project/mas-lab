#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for library-telemetry tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# opentelemetry-sdk is optional; conversion tests skip cleanly when it is absent.
try:
    import opentelemetry.sdk  # noqa: F401

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    OTEL_AVAILABLE = False

requires_otel = pytest.mark.skipif(not OTEL_AVAILABLE, reason="opentelemetry-sdk not installed")


@pytest.fixture
def events_path() -> Path:
    """Path to the canonical native events.jsonl fixture."""
    return FIXTURES / "events.jsonl"


@pytest.fixture
def events(events_path: Path) -> list[dict]:
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


@pytest.fixture
def spans_path(tmp_path: Path, events_path: Path) -> Path:
    """Replay the fixture events to an otel_sdk_spans.jsonl and return its path."""
    if not OTEL_AVAILABLE:  # pragma: no cover
        pytest.skip("opentelemetry-sdk not installed")
    from mas.library.telemetry.conversion.replay import replay_events_file

    out = tmp_path / "otel_sdk_spans.jsonl"
    replay_events_file(
        events_path,
        out,
        service_name="mas-runtime",
        app_name="test-app",
        converter_profile="raw",
    )
    return out


@pytest.fixture
def spans(spans_path: Path) -> list[dict]:
    return [json.loads(line) for line in spans_path.read_text().splitlines() if line.strip()]


@pytest.fixture
def observe_sdk_spans(tmp_path: Path, events_path: Path) -> list[dict]:
    """Replay canonical events with the observe_sdk converter profile."""
    if not OTEL_AVAILABLE:  # pragma: no cover
        pytest.skip("opentelemetry-sdk not installed")
    from mas.library.telemetry.conversion.replay import replay_events_file

    out = tmp_path / "observe_sdk_spans.jsonl"
    replay_events_file(
        events_path,
        out,
        service_name="mas-runtime",
        app_name="test-app",
        converter_profile="observe_sdk",
    )
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
