#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for library-telemetry tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import opentelemetry.sdk  # noqa: F401  (part of the [dev] extra; a missing install fails here)
import pytest

# test-only helpers shared with the other library suites; shipped in no package
_SUPPORT = Path(__file__).resolve().parents[2] / "tests" / "support"
if str(_SUPPORT) not in sys.path:
    sys.path.insert(0, str(_SUPPORT))

import scope_next  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


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


def pytest_addoption(parser):
    scope_next.add_option(parser)


def pytest_configure(config):
    scope_next.add_marker(config)


def pytest_collection_modifyitems(config, items):
    scope_next.skip_unless_enabled(config, items)
