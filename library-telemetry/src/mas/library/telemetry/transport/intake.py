#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Attach a SpanProcessor to the process TracerProvider without exporting.

OpenTelemetry is imported only inside functions. Off (no call to
``install_intake``) means this module is never imported by the binder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Mapping

from mas.library.telemetry.transport.registry import ClassifierSet
from mas.library.telemetry.transport.warnings import warn_once
from mas.runtime.schema.transport import (
    INTERNAL,
    SOURCE_SDK_SPAN,
    TransportEvent,
)
from mas.runtime.transport.classifier import (
    Drop,
    Emit,
    Passthrough,
    SpanView,
    TransportClassifier,
)
from mas.runtime.transport.emit import emit_transport, protocol_config

_logger = logging.getLogger("mas.library.telemetry.transport")

_ATTR_CAP = 4096

# One processor per provider: a second ``install_intake`` (per-instance setup) must not double every event.
_installed: dict[int, tuple[Any, "IntakeReport"]] = {}
_installed_lock = Lock()


@dataclass(frozen=True)
class IntakeConfig:
    """Caller-supplied intake options. No environment reads."""

    attribute_cap: int = _ATTR_CAP
    sdk_spans_requested: bool = False
    sdk_imported_first: bool = False


@dataclass
class IntakeReport:
    active: bool
    reason: str = ""
    provider: str = ""
    classifiers: tuple[str, ...] = ()
    sdk_spans: str = "off"
    seen_names: dict[str, set[str]] = field(default_factory=dict)


def _cap_attrs(attrs: Mapping[str, Any], cap: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    truncated = False
    for key, value in attrs.items():
        if isinstance(value, (bytes, bytearray)):
            text = bytes(value)
            if len(text) > cap:
                out[str(key)] = text[:cap]
                truncated = True
            else:
                out[str(key)] = text
            continue
        text = value if isinstance(value, str) else str(value)
        if isinstance(value, str) and len(value) > cap:
            out[str(key)] = value[:cap]
            truncated = True
        elif not isinstance(value, (str, int, float, bool)) and len(text) > cap:
            out[str(key)] = text[:cap]
            truncated = True
        else:
            out[str(key)] = value
    if truncated:
        out["truncated"] = True
    return out


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def _span_view(span: Any) -> SpanView:
    ctx = span.get_span_context()
    parent = span.parent
    scope = getattr(span, "instrumentation_scope", None) or getattr(span, "instrumentation_info", None)
    attrs = dict(getattr(span, "attributes", None) or {})
    status = getattr(getattr(span, "status", None), "status_code", None)
    return SpanView(
        name=str(getattr(span, "name", "") or ""),
        scope_name=str(getattr(scope, "name", "") or ""),
        scope_version=str(getattr(scope, "version", "") or ""),
        kind=str(getattr(getattr(span, "kind", None), "name", "") or ""),
        attributes=attrs,
        span_id=format(getattr(ctx, "span_id", 0) or 0, "016x"),
        parent_span_id=format(getattr(parent, "span_id", 0) or 0, "016x") if parent else "",
        trace_id=format(getattr(ctx, "trace_id", 0) or 0, "032x"),
        status=str(getattr(status, "name", "") or status or ""),
        start_time_ns=int(getattr(span, "start_time", 0) or 0),
        end_time_ns=int(getattr(span, "end_time", 0) or 0),
    )


def _emit_from_fields(
    fields: Mapping[str, Any],
    span: SpanView,
    *,
    extra: Mapping[str, Any] | None = None,
    cap: int,
) -> None:
    merged = dict(fields)
    if extra:
        attrs = dict(merged.get("attributes") or {})
        attrs.update(extra)
        merged["attributes"] = attrs
    attrs = _cap_attrs(dict(merged.get("attributes") or span.attributes), cap)
    attrs.setdefault("span_name", span.name)
    event = TransportEvent(
        kind=str(merged.get("kind") or INTERNAL),
        protocol=str(merged.get("protocol") or ""),
        side=str(merged.get("side") or ("server" if span.kind == "SERVER" else "client")),
        correlation_key=str(merged.get("correlation_key") or span.span_id),
        source=SOURCE_SDK_SPAN,
        timestamp=_as_float(merged.get("timestamp"), (span.end_time_ns or span.start_time_ns) / 1e9),
        session_id=str(merged.get("session_id") or ""),
        agent_id=str(merged.get("agent_id") or ""),
        call_id=str(merged.get("call_id") or ""),
        run_id=str(merged.get("run_id") or ""),
        mas_id=str(merged.get("mas_id") or ""),
        attributes=attrs,
    )
    emit_transport(event)


class _Processor:
    """SpanProcessor that never attach/detach-s context. on_start/on_end only."""

    def __init__(
        self,
        classifiers: ClassifierSet,
        config: IntakeConfig,
        seen: dict[str, set[str]],
        lock: Lock,
    ) -> None:
        self._classifiers = classifiers
        self._config = config
        self._seen = seen
        self._lock = lock

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        del parent_context
        self._handle(span, start=True)

    def on_end(self, span: Any) -> None:
        self._handle(span, start=False)

    def shutdown(self) -> None:
        self._expected_name_check()

    def force_flush(self, timeout_millis: int = 0) -> bool:
        del timeout_millis
        return True

    def _expected_name_check(self) -> None:
        for classifier in self._classifiers.classifiers:
            cid = type(classifier).__name__
            mapped = set(classifier.mapped_span_names())
            if not mapped:
                continue
            with self._lock:
                seen = set()
                for scope in classifier.scope_names():
                    seen |= self._seen.get(scope, set())
            # A process is a client or a server, on one transport, so some mapped names never appear in a
            # healthy run. None of them appearing while the SDK ran means the names changed.
            if seen and not (mapped & seen):
                warn_once(
                    "expected_name",
                    cid,
                    "transport classifier %s: none of its mapped span names was seen (saw %s); "
                    "the SDK may have renamed them",
                    cid,
                    sorted(seen)[:5],
                )

    def _handle(self, span: Any, *, start: bool) -> None:
        """Never raises: this runs inside the protocol caller's ``start_span`` / ``end``."""
        try:
            scope = getattr(span, "instrumentation_scope", None) or getattr(span, "instrumentation_info", None)
            classifier = self._classifiers.for_scope(str(getattr(scope, "name", "") or ""))
            if classifier is None:
                return  # a span of another library: no copy, no work
            cfg = protocol_config(str(getattr(classifier, "protocol", "") or ""))
            if not cfg.enabled or not cfg.sdk_spans:
                return
            view = _span_view(span)
            with self._lock:
                self._seen.setdefault(view.scope_name, set()).add(view.name)
            try:
                decision = classifier.classify_start(view) if start else classifier.classify_end(view)
            except Exception as exc:
                warn_once(
                    "classifier_error",
                    f"{type(classifier).__name__}:{type(exc).__name__}",
                    "transport classifier %s raised %s on span %s",
                    type(classifier).__name__,
                    type(exc).__name__,
                    view.name,
                )
                decision = Passthrough() if not start else Drop()
            self._apply(decision, view, classifier, start=start)
        except Exception as exc:
            warn_once(
                "intake_error",
                type(exc).__name__,
                "transport intake failed on a span (%s); span ignored",
                type(exc).__name__,
            )

    def _apply(
        self,
        decision: Any,
        view: SpanView,
        classifier: TransportClassifier,
        *,
        start: bool,
    ) -> None:
        cap = self._config.attribute_cap
        if isinstance(decision, Drop):
            return
        if isinstance(decision, Emit):
            _emit_from_fields(decision.event_fields, view, cap=cap)
            return
        if start:
            return  # an unmapped span is announced once, when it ends and its attributes are complete
        _emit_from_fields(
            {
                "kind": INTERNAL,
                "protocol": str(getattr(classifier, "protocol", "") or ""),
                "correlation_key": view.span_id,
            },
            view,
            extra={"span_name": view.name, **dict(view.attributes)},
            cap=cap,
        )


def _as_sdk_processor(processor: _Processor) -> Any:
    """Wrap as an SDK SpanProcessor so on_end is invoked as _on_ending."""
    from opentelemetry.sdk.trace import SpanProcessor

    class Adapter(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            processor.on_start(span, parent_context)

        def on_end(self, span: Any) -> None:
            processor.on_end(span)

        def shutdown(self) -> None:
            processor.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return processor.force_flush(timeout_millis)

    return Adapter()


def _sdk_provider_type() -> type | None:
    try:
        from opentelemetry.sdk.trace import TracerProvider as SdkProvider
    except ImportError:
        return None
    return SdkProvider


def install_intake(
    classifiers: tuple[TransportClassifier, ...] | ClassifierSet,
    *,
    config: IntakeConfig | None = None,
    sink: Callable[[TransportEvent], None] | None = None,
) -> IntakeReport:
    """Attach our processor. Never replaces a non-SDK provider. Never exports."""
    del sink  # events go through emit_transport; sink is accepted for the plan's signature
    cfg = config or IntakeConfig()
    if cfg.sdk_imported_first and cfg.sdk_spans_requested:
        warn_once(
            "sdk_imported_first",
            "",
            "sdk_spans=unavailable (sdk imported first); running on typed hooks only",
        )
    packed = classifiers if isinstance(classifiers, ClassifierSet) else ClassifierSet(classifiers=tuple(classifiers))
    if not packed.classifiers:
        return IntakeReport(active=False, reason="no classifiers", sdk_spans="off")

    try:
        from opentelemetry import trace as otel_trace
    except ImportError:
        _logger.error("transport intake: OpenTelemetry is not installed")
        return IntakeReport(active=False, reason="opentelemetry missing")

    from mas.library.telemetry.transport.version import check_sdk_range

    for classifier in packed.classifiers:
        distribution = str(getattr(classifier, "sdk_distribution", "") or "")
        if distribution:
            check_sdk_range(
                classifier_id=type(classifier).__name__,
                specifier=classifier.supported_sdk_range(),
                distribution=distribution,
            )

    sdk_cls = _sdk_provider_type()
    if sdk_cls is None:
        _logger.error("transport intake: opentelemetry SDK is not installed")
        return IntakeReport(active=False, reason="opentelemetry SDK missing")

    current = otel_trace.get_tracer_provider()
    current_type = type(current).__name__
    proxy = current_type in {"ProxyTracerProvider", "_DefaultTracerProvider"}
    is_sdk = isinstance(current, sdk_cls)
    if not is_sdk and not proxy:
        _logger.error(
            "transport intake: existing TracerProvider %s is not an SDK provider; not replaced",
            current_type,
        )
        return IntakeReport(
            active=False,
            reason=f"non-SDK provider {current_type}",
            provider=current_type,
            classifiers=packed.ids(),
        )

    if proxy:
        provider = sdk_cls()
        otel_trace.set_tracer_provider(provider)
        provider_name = "created-sdk"
    else:
        provider = current
        provider_name = "existing-sdk"

    with _installed_lock:
        # One lock from check to registration: two threads must not both attach a processor.
        existing = _installed.get(id(provider))
        if existing is not None and existing[0] is provider:
            return IntakeReport(
                active=True,
                reason="already installed",
                provider="existing-sdk" if not proxy else "created-sdk",
                classifiers=packed.ids(),
                sdk_spans=existing[1].sdk_spans,
                seen_names=existing[1].seen_names,
            )
        seen: dict[str, set[str]] = {}
        processor = _Processor(packed, cfg, seen, Lock())
        provider.add_span_processor(_as_sdk_processor(processor))
        sdk_spans = "on" if cfg.sdk_spans_requested and not cfg.sdk_imported_first else "off"
        if cfg.sdk_imported_first and cfg.sdk_spans_requested:
            sdk_spans = "unavailable (sdk imported first)"
        report = IntakeReport(
            active=True,
            provider=provider_name,
            classifiers=packed.ids(),
            sdk_spans=sdk_spans,
            seen_names=seen,
        )
        _installed[id(provider)] = (provider, report)
    _logger.info(
        "transport intake active provider=%s classifiers=%s sdk_spans=%s",
        report.provider,
        ",".join(report.classifiers) or "-",
        report.sdk_spans,
    )
    return report


__all__ = ["IntakeConfig", "IntakeReport", "install_intake"]
