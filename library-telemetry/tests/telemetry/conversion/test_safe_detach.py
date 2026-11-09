#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Cross-context OTel detach must not ERROR-log (issue #127)."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

import pytest
from mas.library.telemetry.conversion.converter import _safe_detach
from mas.library.telemetry.conversion.exporter import OTEL_AVAILABLE

pytestmark = pytest.mark.skipif(not OTEL_AVAILABLE, reason="opentelemetry-sdk not installed")


def test_safe_detach_from_another_thread_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    from opentelemetry import context as context_api
    from opentelemetry import trace

    token_box: list[object] = []

    def attach_in_worker() -> None:
        span = trace.NonRecordingSpan(trace.INVALID_SPAN_CONTEXT)
        token_box.append(context_api.attach(trace.set_span_in_context(span)))

    worker = threading.Thread(target=attach_in_worker)
    worker.start()
    worker.join()

    with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
        _safe_detach(token_box[0])

    assert not caplog.records


def test_native_observability_import_does_not_load_otel_plugin() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import mas.library.standard.plugins.observability.native_plugin; "
            "print('otel_plugin' if 'mas.library.telemetry.plugins.otel_plugin' in sys.modules else 'ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ok"
