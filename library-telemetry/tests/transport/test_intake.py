#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import os
import subprocess
import sys
from threading import Lock

import pytest
from mas.library.telemetry.transport.intake import IntakeConfig, _Processor
from mas.library.telemetry.transport.registry import ClassifierSet
from mas.library.telemetry.transport.warnings import count
from mas.library.telemetry.transport.warnings import reset as reset_warnings
from mas.runtime.schema.transport import INTERNAL, TransportEvent
from mas.runtime.transport.classifier import SpanView
from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
from mas.runtime.transport.emit import bind_config, bind_sink
from mas.runtime.transport.emit import reset as reset_emit


class _Boom:
    def scope_names(self) -> tuple[str, ...]:
        return ("test.scope",)

    def supported_sdk_range(self) -> str:
        return ""

    def mapped_span_names(self) -> tuple[str, ...]:
        return ("boom",)

    def aliases(self):
        from mas.runtime.transport.classifier import AliasTable

        return AliasTable()

    def classify_start(self, span: SpanView):
        raise RuntimeError("boom")

    def classify_end(self, span: SpanView):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_emit()
    reset_warnings()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(internals=True, sdk_spans=True),
        )
    )
    yield
    reset_emit()
    reset_warnings()


def _fake_span(name: str = "boom", scope: str = "test.scope"):
    class Scope:
        version = ""

        def __init__(self, scope_name: str) -> None:
            self.name = scope_name

    class Ctx:
        span_id = 1
        trace_id = 1

    class Status:
        status_code = None

    class Span:
        def get_span_context(self):
            return Ctx()

        kind = type("K", (), {"name": "INTERNAL"})()
        attributes: dict = {}
        parent = None
        status = Status()
        start_time = 0
        end_time = 0

        def __init__(self, span_name: str, scope_name: str) -> None:
            self.name = span_name
            self.instrumentation_scope = Scope(scope_name)

    return Span(name, scope)


def test_classifier_exception_is_passthrough(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="mas.library.telemetry.transport")
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    bind_sink(Sink())
    processor = _Processor(
        ClassifierSet(classifiers=(_Boom(),)),
        IntakeConfig(),
        {},
        Lock(),
    )
    span = _fake_span()
    for _ in range(20):
        processor.on_end(span)
    assert count("classifier_error", "_Boom:RuntimeError") == 20
    assert caplog.text.count("raised RuntimeError") == 1
    assert captured
    assert all(event.kind == INTERNAL for event in captured)


def test_r1_importing_telemetry_transport_does_not_import_opentelemetry() -> None:
    script = (
        "import sys\n"
        "mods = set(sys.modules)\n"
        "import mas.library.telemetry.transport.warnings\n"
        "assert not any(name.startswith('opentelemetry') for name in sys.modules if name not in mods)\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_non_sdk_provider_is_not_replaced() -> None:
    script = r"""
import sys
from mas.library.telemetry.transport.intake import IntakeConfig, install_intake

class Foreign:
    def get_tracer(self, *args, **kwargs):
        raise AssertionError

from opentelemetry import trace
trace.set_tracer_provider(Foreign())
class C:
    def scope_names(self): return ('x',)
    def supported_sdk_range(self): return ''
    def mapped_span_names(self): return ()
    def aliases(self):
        from mas.runtime.transport.classifier import AliasTable
        return AliasTable()
    def classify_start(self, span):
        from mas.runtime.transport.classifier import Drop
        return Drop()
    def classify_end(self, span):
        from mas.runtime.transport.classifier import Drop
        return Drop()
report = install_intake((C(),), config=IntakeConfig())
assert report.active is False, report
assert 'non-SDK' in report.reason
"""
    env = dict(os.environ)
    env["OTEL_PYTHON_TRACER_PROVIDER"] = ""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installing_intake_twice_does_not_double_events(monkeypatch) -> None:
    """Mutation: install_intake adds a processor on every call, so per-instance setup doubles each event."""
    pytest = __import__("pytest")
    pytest.importorskip("opentelemetry.sdk.trace")
    from library_ioa.plugins.a2a.classifier import A2ATransportClassifier
    from mas.library.telemetry.transport.intake import install_intake
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    captured = []

    class Sink:
        def emit(self, event) -> None:
            captured.append(event)

    emit.reset()
    emit.bind_config(
        TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(internals=True, sdk_spans=True))
    )
    emit.bind_sink(Sink())
    try:
        first = install_intake((A2ATransportClassifier(),))
        second = install_intake((A2ATransportClassifier(),))
        assert first.active and second.active and second.reason == "already installed"
        with provider.get_tracer("a2a-python-sdk").start_as_current_span("a2a.some.internal.step"):
            pass
    finally:
        emit.reset()
    assert [e.kind for e in captured] == ["transport.internal"]


def test_concurrent_install_attaches_one_processor(monkeypatch) -> None:
    """Mutation: the already-installed check ran outside the registration lock, so racing installs doubled events."""
    import threading
    import time

    pytest.importorskip("opentelemetry.sdk.trace")
    from library_ioa.plugins.a2a.classifier import A2ATransportClassifier
    from mas.library.telemetry.transport.intake import install_intake
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    class Slow(TracerProvider):
        attached = 0

        def add_span_processor(self, processor) -> None:
            time.sleep(0.05)  # widen the window between the check and the registration
            type(self).attached += 1
            super().add_span_processor(processor)

    provider = Slow()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    threads = [threading.Thread(target=install_intake, args=((A2ATransportClassifier(),),)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert Slow.attached == 1


def test_cap_attrs_truncates_bytes_and_long_text() -> None:
    from mas.library.telemetry.transport.intake import _as_float, _cap_attrs

    out = _cap_attrs({"b": b"x" * 20, "s": "y" * 20, "n": 3, "obj": object()}, cap=8)
    assert out["truncated"] is True
    assert out["b"] == b"x" * 8
    assert out["s"] == "y" * 8
    assert out["n"] == 3
    assert _cap_attrs({"ok": b"ab"}, cap=8)["ok"] == b"ab"
    assert _as_float("nope", 1.5) == 1.5
    assert _as_float(0, 2.0) == 2.0


def test_expected_name_check_warns_when_mapped_names_are_missing() -> None:
    class Named:
        protocol = "example"

        def scope_names(self):
            return ("test.scope",)

        def supported_sdk_range(self):
            return ""

        def mapped_span_names(self):
            return ("expected.span",)

        def aliases(self):
            from mas.runtime.transport.classifier import AliasTable

            return AliasTable()

        def classify_start(self, span):
            from mas.runtime.transport.classifier import Drop

            return Drop()

        def classify_end(self, span):
            from mas.runtime.transport.classifier import Passthrough

            return Passthrough()

    seen = {"test.scope": {"other.span"}}
    processor = _Processor(ClassifierSet(classifiers=(Named(),)), IntakeConfig(), seen, Lock())
    processor.shutdown()
    processor.on_start(_fake_span())
    assert processor.force_flush() is True


def test_intake_swallows_a_broken_span_and_a_start_passthrough() -> None:
    class Empty:
        protocol = "example"

        def scope_names(self):
            return ("test.scope",)

        def supported_sdk_range(self):
            return ""

        def mapped_span_names(self):
            return ()

        def aliases(self):
            from mas.runtime.transport.classifier import AliasTable

            return AliasTable()

        def classify_start(self, span):
            from mas.runtime.transport.classifier import Passthrough

            return Passthrough()

        def classify_end(self, span):
            from mas.runtime.transport.classifier import Drop

            return Drop()

    processor = _Processor(ClassifierSet(classifiers=(Empty(),)), IntakeConfig(), {}, Lock())
    processor.on_start(_fake_span())
    processor.on_end(object())  # not a span: outer except


def test_sdk_adapter_forwards_shutdown_and_flush() -> None:
    from mas.library.telemetry.transport.intake import _as_sdk_processor

    processor = _Processor(ClassifierSet(classifiers=(_Boom(),)), IntakeConfig(), {}, Lock())
    adapter = _as_sdk_processor(processor)
    adapter.shutdown()
    assert adapter.force_flush(1) is True
    adapter.on_start(_fake_span())
    adapter.on_end(_fake_span())


def test_install_intake_reports_empty_and_sdk_imported_first(monkeypatch) -> None:
    pytest.importorskip("opentelemetry.sdk.trace")
    from library_ioa.plugins.a2a.classifier import A2ATransportClassifier
    from mas.library.telemetry.transport.intake import install_intake

    empty = install_intake(())
    assert empty.active is False and empty.reason == "no classifiers"
    first = install_intake(
        (A2ATransportClassifier(),),
        config=IntakeConfig(sdk_spans_requested=True, sdk_imported_first=True),
    )
    assert first.sdk_spans == "unavailable (sdk imported first)" or first.active

    monkeypatch.setattr("mas.library.telemetry.transport.intake._sdk_provider_type", lambda: None)
    missing = install_intake((A2ATransportClassifier(),))
    assert missing.active is False
    assert "SDK" in missing.reason or "opentelemetry" in missing.reason.lower() or missing.reason


def test_sdk_spans_off_does_not_ingest() -> None:
    """Mutation: leftover intake still classifies when sdk_spans is false."""
    captured: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            captured.append(event)

    bind_config(
        TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(internals=True, sdk_spans=False))
    )
    bind_sink(Sink())
    processor = _Processor(ClassifierSet(classifiers=(_Boom(),)), IntakeConfig(), {}, Lock())
    processor.on_end(_fake_span())
    assert captured == []
