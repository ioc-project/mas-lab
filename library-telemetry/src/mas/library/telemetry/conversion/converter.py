#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``MasOtelConverter`` — native ``events.jsonl`` → OTel spans.

This is the inverse of ``library-kg``'s OTel→KG normaliser: it takes MAS native
event records and produces OTel spans via a supplied tracer.

Design (mirrors library-kg)
---------------------------
* **Thin, stateful core.**  This class owns only span-tree state (open spans,
  parent-context linking, call-id scoping) and a small set of *primitive*
  emission methods (:meth:`open_span`, :meth:`close_span`, :meth:`point_span`).
* **Mapping lives in categories.**  The per-``kind`` logic lives in
  :mod:`mas.library.telemetry.conversion.mappings` category modules, registered
  through a decorator registry.  The dispatch table is assembled once via
  :func:`~mas.library.telemetry.conversion.mappings.base.build_handler_table`.
  Adding a new event kind never touches this file.
* **1:1 otherwise.**  Span names and attributes come from the native event
  fields. The only remapping is :mod:`.delegation` (``delegate_to_*`` →
  observe-sdk handoff / visit split). Session/graph bookends are the other
  high-level exceptions.

Two modes of use
----------------
Live (during agent execution): the plugin builds a minimal event dict per hook
and calls :meth:`process_event`.

Offline replay (from ``events.jsonl``): see
:func:`mas.library.telemetry.conversion.replay.replay_events_file`.

Parent-span correlation uses ``call_id`` / ``parent_call_id``; accurate
timestamps come from ``event["timestamp"]`` (float seconds → nanoseconds).

``OBSERVE_REALTIME_OBSERVABILITY_ENABLED`` is pinned to ``false`` by default
in :mod:`mas.library.telemetry` (the package ``__init__.py``, always
imported before this submodule) — not repeated here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, ClassVar, Dict, Optional, Set, Tuple

from mas.library.telemetry.conversion.envelope import envelope_for_kind
from mas.library.telemetry.conversion.exporter import OTEL_AVAILABLE
from mas.library.telemetry.conversion.layers import ExportLayers, should_export_event
from mas.library.telemetry.conversion.mappings.base import Handler, build_handler_table
from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.delegation import (
    ensure_resumed_agent_call as _ensure_resumed_agent_call,
    resolve_agent_end_call_id as _resolve_agent_end_call_id,
    rewrite_delegate_tool_end as _rewrite_delegate_tool_end,
    rewrite_delegate_tool_start as _rewrite_delegate_tool_start,
)
from mas.library.telemetry.conversion.profiles import (
    default_extensions,
    normalize_converter_profile,
)
from mas.library.telemetry.exceptions import OtelSdkUnavailableError

logger = logging.getLogger(__name__)


class SpanIdReuse:
    """Id generator that can force the next emitted span id."""

    def reuse_span_id(self, span_id: int) -> None:
        raise NotImplementedError

if OTEL_AVAILABLE:
    from opentelemetry import context as context_api
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan
else:  # pragma: no cover
    context_api = None  # type: ignore[assignment]
    trace = None  # type: ignore[assignment]
    NonRecordingSpan = None  # type: ignore[assignment]


def _safe_detach(token: Any) -> None:
    """Detach an OTel context token without ERROR-logging cross-context resets.

    ``opentelemetry.context.detach`` catches ``ValueError`` internally and
    logs a full traceback at ERROR. Tokens created on one worker thread or
    asyncio task and reset on another produce the issue #127 log explosion.
    Bypass the logging wrapper and ignore that ``ValueError``.
    """
    if token is None or not OTEL_AVAILABLE or context_api is None:
        return
    runtime = getattr(context_api, "_RUNTIME_CONTEXT", None)
    try:
        if runtime is not None:
            runtime.detach(token)
        else:
            context_api.detach(token)
    except ValueError:
        # The one documented, expected failure mode (issue #127) -- anything
        # else is a real bug and should propagate, not be swallowed here too.
        return


class MasOtelConverter:
    """Convert MAS native event records to OTel spans.

    A single converter instance is stateful: it tracks open spans by ``call_id``
    across multiple :meth:`process_event` calls.  One converter instance
    corresponds to one agent session (or one replayed run).

    Not thread-safe: span maps are mutated without locking (single-threaded use).

    Parameters
    ----------
    tracer:
        An initialised OTel ``Tracer`` (from ``provider.get_tracer(...)``).
    app_name:
        Optional application id stamped on every span (``application_id`` /
        ``session.name``).
    export_layers:
        Which observability layers to emit; defaults to structure + execution +
        trajectory + semantic (OXP-required set; governance remains opt-in).
    """

    # Sentinel run_id / session values that must never become identifiers.
    _SENTINEL_RUN_IDS: ClassVar[frozenset] = frozenset({"local", "unknown"})
    _TRACE_ROOT_CALL_ID: ClassVar[str] = "_mas_trace_root"

    # Boundaries whose observe_sdk-profile span shape is meant to mirror
    # what the real ioa-observe-sdk itself would emit for the same call
    # (verified empirically against the installed SDK, not guessed).
    _REAL_SDK_MIRRORED_BOUNDARIES: ClassVar[frozenset] = frozenset(
        {"TaskCall", "AgentCall", "LLMCall", "ToolCall", "Session", "AgentLifecycleEvent"}
    )

    # noa-trip-planner / OXP ingest categories. Everything else is an
    # extension (opt-in) or a CallAnnotation (opt-in via annotation_enabled).
    _OBSERVE_SDK_CORE_BOUNDARIES: ClassVar[frozenset[str]] = frozenset(
        {
            "Session",
            "Graph",
            "TaskCall",
            "AgentCall",
            "LLMCall",
            "ToolCall",
            "RealtimeSignal",
            "AgentLifecycleEvent",
        }
    )
    _OBSERVE_SDK_EXTENSION_BOUNDARIES: ClassVar[frozenset[str]] = frozenset(
        {
            "ProcessingCall",
            "ContextContribution",
            "GovernanceEvent",
            "MemoryCall",
            "SkillCall",
            "SkillExecution",
            "RAGQuery",
            "Worker",
            "NetworkCall",
            "WorkflowTransition",
            "AgentCommunication",
        }
    )

    # Assembled once at class definition (imports category modules for side effects).
    _HANDLERS: ClassVar[Dict[str, Handler]] = build_handler_table()

    def __init__(
        self,
        tracer: Any,
        app_name: str = "",
        export_layers: ExportLayers | None = None,
        converter_profile: str | None = None,
        annotation_enabled: bool | None = None,
        realtime: bool = False,
        extensions: bool | None = None,
        timestamp_offset_s: float = 0.0,
        session_uuid: str | None = None,
        rewrite_tool_delegation: bool = True,
        agent_llm_models: Dict[str, str] | None = None,
        id_generator: Any | None = None,
    ) -> None:
        if not OTEL_AVAILABLE:
            raise OtelSdkUnavailableError()
        self.tracer = tracer
        # Only used by emit_realtime_signal, and only if it exposes
        # reuse_span_id() (see session.py's _SharedTraceIdGenerator).
        self._id_generator = id_generator
        from mas.library.telemetry.conversion.topology import declared_mas_name

        self._app_name = declared_mas_name(app_name)
        self._export_layers = export_layers or ExportLayers()
        self._converter_profile = normalize_converter_profile(converter_profile)
        self._annotation_enabled = (
            self._converter_profile != "observe_sdk"
            if annotation_enabled is None
            else bool(annotation_enabled)
        )
        self._run_id: str = ""
        self._session_uuid: str = str(session_uuid or uuid.uuid4())
        self._session_uuid_pinned: bool = bool(session_uuid)
        # call_id → (span, context_token)
        self._open_spans: Dict[str, Tuple[Any, Any]] = {}
        # call_id → SpanContext for closed interval spans (point-span parent linking)
        self._closed_span_ctx: Dict[str, Any] = {}
        # call_ids whose structural span already received a matching *_end
        self._closed_call_ids: Set[str] = set()
        # call_id → agent_id (disambiguate shared ids across agents in one trace)
        self._call_id_agents: Dict[str, str] = {}
        # call_id -> lightweight span metadata (for close-time profile overlays)
        self._span_meta: Dict[str, Dict[str, Any]] = {}
        self._realtime = bool(realtime)
        self._extensions = default_extensions(self._converter_profile, extensions)
        # agent_id → OTel span id of the open/last AgentCall (OXP linkage).
        self._agent_span_ids: Dict[str, str] = {}
        self._agent_trace_ids: Dict[str, str] = {}
        # call_id -> tool/model name, for emit_realtime_signal's *_end event:
        # by the time it runs the regular handler has already closed the
        # span and popped _span_meta, so the *_start event's own name has
        # to survive somewhere else.
        self._realtime_entity_names: Dict[str, str] = {}
        # call_id -> the "*_start" signal's own (int) span id, so the
        # matching "*_completed"/"*_end" signal can reuse it (see
        # emit_realtime_signal) instead of minting an unrelated one -- the
        # two are otherwise two independent spans with no way to tell norm
        # they describe the same logical call.
        self._realtime_span_ids: Dict[str, int] = {}
        self._last_agent_span_id: str = ""
        self._last_agent_trace_id: str = ""
        self._last_agent_id: str = ""
        self._agent_sequence: int = 0
        # rewrite_tool_delegation: intercept delegate_to_<id> tools (default on).
        self._rewrite_tool_delegation = bool(rewrite_tool_delegation)
        # target agent_id → {caller, call_id} for the open rewritten handoff.
        self._pending_delegations: Dict[str, Dict[str, str]] = {}
        self._rewritten_delegate_ids: Set[str] = set()
        self._orphan_call_end_total: int = 0
        self._agent_llm_models: Dict[str, str] = {
            str(key): str(value)
            for key, value in (agent_llm_models or {}).items()
            if key and value
        }
        self._seen_events: list[Dict[str, Any]] = []
        self._graph_emitted: bool = False
        self._session_start_emitted: bool = False
        self._session_end_emitted: bool = False
        self._timestamp_offset_s = float(timestamp_offset_s or 0.0)
        # parent AgentCall call_id → last user-facing output (user_response)
        self._agent_outputs: Dict[str, str] = {}
        self._shared_trace_id: int | None = None
        # After a rewritten delegate_to_*, the caller AgentCall is closed and
        # the next LLM/user_response opens a new visit (LangGraph-style).
        self._resume_pending: Dict[str, Dict[str, Any]] = {}
        self._resume_ids: Dict[str, str] = {}
        self._agent_visit_count: Dict[str, int] = {}
        self._last_closed_agent_end_ns: Optional[int] = None
        self._agent_end_ns: Dict[str, int] = {}
        self._mas_call_end_ts: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_event(self, event: Dict[str, Any]) -> None:
        """Dispatch a single native event record to the appropriate handler."""
        event = self._scope_event_call_ids(event)
        event = self._inherit_agent_id(event)
        self._seen_events.append(event)
        self._ensure_trace_root(self.ts_ns(event))
        kind = str(event.get("kind") or "")
        if kind == "mas_call_end":
            ts = event.get("timestamp")
            if isinstance(ts, (int, float)):
                self._mas_call_end_ts = float(ts)
        if not should_export_event(event, self._export_layers):
            self._maybe_end_session(kind)
            return
        run_id = event.get("run_id") or (event.get("context") or {}).get("run_id", "")
        if run_id and run_id not in self._SENTINEL_RUN_IDS:
            self._run_id = run_id
        explicit_session_id = str(
            event.get("session_id")
            or (event.get("context") or {}).get("session_id")
            or ""
        ).strip()
        if (
            explicit_session_id
            and explicit_session_id not in self._SENTINEL_RUN_IDS
            and not self._session_uuid_pinned
        ):
            self._session_uuid = explicit_session_id
            self._session_uuid_pinned = True
        if (
            self._converter_profile == "observe_sdk"
            and not self._session_start_emitted
            and explicit_session_id
        ):
            # Trigger only on the event that actually carries a session id,
            # never merely because replay pre-resolved _session_uuid from a
            # whole-file scan — otherwise replay fires session.start on
            # event #1 while live (which can't see ahead) fires it on
            # whichever event first names the session, giving the two
            # paths different session.start timestamps for the same run.
            self.emit_session_start(event)
        handler = self._HANDLERS.get(kind)
        if handler:
            handler(self, event)
        else:
            self._emit_generic_event(event)
        if self._realtime:
            self.emit_realtime_signal(event)
        self._maybe_end_session(kind)

    def flush_open_spans(self, *, status: str = "success") -> None:
        """End any spans still open so exporters receive them."""
        for call_id in list(self._open_spans):
            self.close_span(call_id, status=status)

    def finish(self, *, emit_graph: bool | None = None) -> None:
        """Emit the end-of-run graph span (unless realtime) and close open spans.

        Live plugin flush and file replay both call this so graph vs realtime
        is decided in one place. ``session.end`` is emitted here as a
        fallback when the run never recorded ``mas_call_end``.
        """
        do_graph = (not self._realtime) if emit_graph is None else bool(emit_graph)
        if do_graph:
            self._emit_graph_bookend()
        for call_id in list(self._open_spans):
            if call_id != self._TRACE_ROOT_CALL_ID:
                self.close_span(call_id, status="success")
        if self._converter_profile == "observe_sdk":
            if not self._session_start_emitted:
                self.emit_session_start()
            self.emit_session_end()
        if self._TRACE_ROOT_CALL_ID in self._open_spans:
            self.close_span(
                self._TRACE_ROOT_CALL_ID,
                end_ns=self._shifted_ts_ns(self._latest_event_ts()),
            )

    def _emit_graph_bookend(self) -> None:
        if self._graph_emitted or not self._seen_events:
            return
        from mas.library.telemetry.conversion.topology import build_topology

        earliest_ts = next(
            (
                event.get("timestamp")
                for event in self._seen_events
                if isinstance(event.get("timestamp"), (int, float))
            ),
            None,
        )
        graph_ts_ns = self._shifted_ts_ns(earliest_ts)
        self.emit_graph_span(
            build_topology(self._seen_events),
            app_name=self._app_name,
            ts_ns=graph_ts_ns,
            parent_call_id=(
                None
                if self._converter_profile == "observe_sdk"
                else self._TRACE_ROOT_CALL_ID
            ),
        )
        self._graph_emitted = True

    def _maybe_end_session(self, kind: str) -> None:
        """``mas_call_end`` is the explicit known end of the MAS run.

        Graph first, then ``session.end``, so ingest sees topology before
        the session closes. Remaining open spans stay for ``finish()``.
        """
        if kind != "mas_call_end" or self._converter_profile != "observe_sdk":
            return
        if not self._realtime:
            self._emit_graph_bookend()
        if not self._session_start_emitted:
            self.emit_session_start()
        self.emit_session_end()

    def _ensure_trace_root(self, start_ns: int | None = None) -> None:
        """Raw profile: one exported ``root`` so the tree shares a TraceId.

        Observe-sdk profile: no exported wrapper. Sibling roots (session,
        graph, ``invoke_agent``) share a TraceId via :meth:`_observe_root_context`.
        """
        if self._converter_profile == "observe_sdk":
            return
        if (
            self._TRACE_ROOT_CALL_ID in self._open_spans
            or self._TRACE_ROOT_CALL_ID in self._closed_call_ids
        ):
            return
        self.open_span(
            self._TRACE_ROOT_CALL_ID,
            "root",
            {"mas.boundary": "TraceRoot"},
            attach_to_root=True,
            start_ns=start_ns,
            set_call_id=False,
        )

    def _observe_root_context(self) -> Any:
        """Parentless context. Shared TraceId comes from the provider generator."""
        return context_api.Context()

    def _emit_generic_event(self, ev: Dict[str, Any]) -> None:
        """Emit a span for any kind that has no dedicated handler.

        Start/end pairs become one interval span; everything else is a point
        span. Disabled export layers still filter the event before this runs.
        """
        kind = str(ev.get("kind") or "event")
        block, _, _ = envelope_for_kind(kind)
        boundary = {
            "structural": "CallAnnotation",
            "execution": "CallAnnotation",
            "context": "CallAnnotation",
            "trajectory": "CallAnnotation",
            "governance": "GovernanceEvent",
        }.get(block, "CallAnnotation")
        attrs: Dict[str, Any] = {
            "mas.boundary": boundary,
            "mas.agent.id": self.agent_id(ev),
            "mas.event.kind": kind,
        }
        if boundary == "CallAnnotation":
            attrs["mas.annotation.kind"] = kind
        elif boundary == "GovernanceEvent":
            attrs["mas.governance.kind"] = kind
            if ev.get("outcome"):
                attrs["mas.governance.outcome"] = ev.get("outcome")
        call_id = ev.get("call_id")
        if kind.endswith("_start"):
            self.open_span(
                self.span_key(ev),
                boundary,
                attrs,
                ev.get("parent_call_id"),
                start_ns=self.ts_ns(ev),
            )
            return
        if kind.endswith("_end"):
            key = self.span_key(ev)
            if self.is_open(key):
                extra = {}
                if ev.get("status"):
                    extra["mas.status"] = ev.get("status")
                self.close_span(
                    key,
                    extra or None,
                    status=ev.get("status", "success"),
                    end_ns=self.ts_ns(ev),
                )
                return
        self.point_span(
            boundary,
            attrs,
            ev.get("parent_call_id"),
            ts_ns=self.ts_ns(ev),
            call_id=call_id,
        )

    def _root_call_id(self) -> str | None:
        for event in self._seen_events:
            if event.get("kind") == "mas_call_start" and event.get("call_id"):
                return str(event["call_id"])
        for event in self._seen_events:
            kind = str(event.get("kind") or "")
            if kind.endswith("_start") and not event.get("parent_call_id") and event.get("call_id"):
                return str(event["call_id"])
        return None

    def reset_run(self) -> None:
        """Clear per-run span state before a new run."""
        self._open_spans.clear()
        self._closed_span_ctx.clear()
        self._call_id_agents.clear()
        self._closed_call_ids.clear()
        self._run_id = ""
        self._session_uuid = str(uuid.uuid4())
        self._session_uuid_pinned = False
        self._agent_span_ids.clear()
        self._last_agent_span_id = ""
        self._last_agent_id = ""
        self._agent_sequence = 0
        self._pending_delegations.clear()
        self._rewritten_delegate_ids.clear()
        self._orphan_call_end_total = 0
        self._seen_events.clear()
        self._graph_emitted = False
        self._session_start_emitted = False
        self._session_end_emitted = False
        self._agent_outputs.clear()
        self._shared_trace_id = None
        self._last_closed_agent_end_ns = None
        self._agent_end_ns.clear()
        self._mas_call_end_ts = None

    # ------------------------------------------------------------------
    # Primitive emission API (the SpanEmitter protocol handlers depend on)
    # ------------------------------------------------------------------

    def open_span(
        self,
        call_id: str,
        name: str,
        attrs: Dict[str, Any],
        parent_call_id: str | None = None,
        start_ns: int | None = None,
        *,
        set_call_id: bool = True,
        attach_to_root: bool = False,
    ) -> int | None:
        """Open an interval span and store it by ``call_id``.

        ``set_call_id`` controls whether ``call_id`` is written as the
        ``mas.call.id`` attribute.  Interval spans use the real call id;
        point spans (see :meth:`point_span`) pass ``False`` so their synthetic
        tracking key never leaks into span attributes.

        Returns the new span's raw (int) span id, or ``None`` if nothing was
        opened (filtered boundary, or the span context couldn't be read) --
        used by :meth:`emit_realtime_signal` to make a later point span
        reuse this exact id (see ``reuse_span_id`` on the active
        ``id_generator``).
        """
        if not self._should_emit_span(str(attrs.get("mas.boundary") or "")):
            return None
        boundary_name = str(attrs.get("mas.boundary") or "")
        if (
            self._converter_profile == "observe_sdk"
            and self._rewrite_tool_delegation
            and boundary_name in {"LLMCall", "ToolCall"}
        ):
            self.ensure_resumed_agent_call(
                {
                    "agent_id": attrs.get("mas.agent.id"),
                    "timestamp": (start_ns / 1e9) if start_ns else None,
                    "parent_call_id": parent_call_id,
                    "call_id": call_id,
                    "input": attrs.get("mas.input") or attrs.get("mas.llm.messages") or "",
                }
            )
        if attach_to_root:
            ctx = (
                self._observe_root_context()
                if self._converter_profile == "observe_sdk"
                else context_api.Context()
            )
        else:
            ctx = self._parent_ctx(
                self._effective_parent_call_id(
                    parent_call_id,
                    str(attrs.get("mas.agent.id") or ""),
                    boundary=str(attrs.get("mas.boundary") or ""),
                )
            )
        overlay: Dict[str, Any] = {}
        boundary_name = str(attrs.get("mas.boundary") or "")
        if self._app_name:
            # application_id is the MAS name. service.name (the resource)
            # is the same MAS: Observe SDK treats the MAS, not each
            # agent, as the service that produces telemetry. Agent names
            # stay on agent_id / mas.agent.id.
            overlay[semconv.APPLICATION_ID] = self._app_name
            if not (
                self._converter_profile == "observe_sdk"
                and boundary_name in self._REAL_SDK_MIRRORED_BOUNDARIES
            ):
                overlay[semconv.SESSION_NAME] = self._app_name
            if self._session_uuid:
                overlay[semconv.SESSION_ID] = semconv.session_id_for(
                    self._app_name, self._session_uuid
                )
        attrs = {**attrs, **overlay}
        if set_call_id and call_id:
            attrs.setdefault("mas.call.id", call_id)
        attrs = self._apply_profile_overlay_on_open(attrs, start_ns)
        name = self._profile_span_name(name, attrs)
        export_attrs = self._observe_sdk_export_attrs(attrs)
        kwargs: Dict[str, Any] = {"context": ctx, "attributes": export_attrs}
        if start_ns is not None:
            kwargs["start_time"] = start_ns
        span = self.tracer.start_span(name, **kwargs)
        token = context_api.attach(trace.set_span_in_context(span))
        self._open_spans[call_id] = (span, token)
        span_id = ""
        trace_id_hex = ""
        raw_span_id: int | None = None
        try:
            ctx = span.get_span_context()
            raw_span_id = ctx.span_id
            span_id = semconv.wire_span_id(ctx.span_id)
            trace_id_hex = semconv.wire_trace_id(ctx.trace_id)
            if self._shared_trace_id is None:
                self._shared_trace_id = ctx.trace_id
        except Exception:  # pragma: no cover
            # Best-effort only: degrades handoff correlation (_agent_span_ids
            # below), never the span itself, which already started above.
            logger.debug("Could not read span_id for handoff correlation", exc_info=True)
        if boundary_name == "AgentCall" and span_id:
            agent = str(attrs.get("mas.agent.id") or "")
            self._agent_span_ids[agent] = span_id
            self._agent_trace_ids[agent] = trace_id_hex
            self._last_agent_span_id = span_id
            self._last_agent_trace_id = trace_id_hex
            if agent:
                self._last_agent_id = agent
            if self._converter_profile == "observe_sdk":
                # The real SDK stamps wall-clock time.time() here, which is
                # fine for a live run (its span start_ns already *is* "now")
                # but would break replay's own determinism guarantee
                # (replaying the same events.jsonl twice must produce
                # byte-identical output) -- use the event's own recorded
                # time instead, falling back to time.time() only when no
                # start_ns is available at all.
                span.set_attribute(
                    semconv.AGENT_CHAIN_START_TIME,
                    (start_ns / 1e9) if start_ns is not None else time.time(),
                )
                self.point_span(
                    semconv.SPAN_NAME_AGENT_START_EVENT,
                    {"mas.boundary": "AgentLifecycleEvent", "agent_id": agent},
                    parent_call_id=call_id,
                    ts_ns=start_ns,
                    events=[
                        (
                            semconv.SPAN_NAME_AGENT_START_EVENT,
                            {"agent_name": agent, "description": "", "type": "agent"},
                        )
                    ],
                )
        self._span_meta[call_id] = {
            "boundary": attrs.get("mas.boundary"),
            "agent": attrs.get("mas.agent.id"),
            "tool_name": attrs.get("mas.tool.name"),
            "llm_messages": attrs.get("mas.llm.messages"),
            "span_id": span_id,
        }
        return raw_span_id

    def close_span(
        self,
        call_id: str | None,
        extra: Dict[str, Any] | None = None,
        status: str = "success",
        end_ns: int | None = None,
    ) -> None:
        """Close the interval span previously opened for ``call_id``."""
        if not call_id:
            return
        key = str(call_id)
        if key not in self._open_spans:
            resolved = self._lookup_span_key(key)
            if resolved:
                key = resolved
        if key not in self._open_spans:
            return
        span, token = self._open_spans.pop(key)
        call_id = key
        meta = self._span_meta.pop(str(call_id), {})
        try:
            self._closed_span_ctx[call_id] = span.get_span_context()
            self._closed_call_ids.add(str(call_id))
            span.set_attribute("mas.status", status)
            for k, v in (extra or {}).items():
                if v is None:
                    continue
                encoded = v if not isinstance(v, (dict, list)) else self.enc(v)
                span.set_attribute(k, encoded)
            self._apply_profile_overlay_on_close(
                span, meta, extra or {}, status=status, call_id=str(call_id), end_ns=end_ns
            )
            if end_ns is not None:
                span.end(end_time=end_ns)
            else:
                span.end()
            if meta.get("boundary") == "AgentCall" and end_ns is not None:
                prev = self._last_closed_agent_end_ns
                self._last_closed_agent_end_ns = (
                    end_ns if prev is None else max(prev, end_ns)
                )
                closed_agent = str(meta.get("agent") or "")
                if closed_agent:
                    self._agent_end_ns[closed_agent] = end_ns
        finally:
            _safe_detach(token)

    def annotate_open_span(self, call_id: str | None, attrs: Dict[str, Any]) -> None:
        """Stamp attributes on an in-flight interval span (e.g. AgentCall output)."""
        key = str(call_id or "")
        if key and key not in self._open_spans:
            resolved = self._lookup_span_key(key)
            if resolved:
                key = resolved
        if not key or key not in self._open_spans:
            return
        span, _token = self._open_spans[key]
        for attr_key, value in attrs.items():
            if value is None or value == "":
                continue
            span.set_attribute(
                attr_key, value if not isinstance(value, (dict, list)) else self.enc(value)
            )

    def llm_model_for(self, agent_id: str) -> str:
        """Spec-declared model for *agent_id* (replay discovery / live overlay)."""
        return str(self._agent_llm_models.get(str(agent_id or "")) or "")

    def attach_assembled_llm_context(self, ev: Dict[str, Any]) -> None:
        """Copy ``context_assembled`` messages onto the open ``LLMCall``.

        Cached ``llm_call_start`` events often omit ``messages`` / ``model``.
        The assembled prompt lives on ``context_assembled`` (same call_id).
        Inspect's tree reads ``gen_ai.input.messages`` via the LLMCall State.
        """
        call_id = str(ev.get("llm_call_id") or ev.get("call_id") or "")
        messages = ev.get("messages")
        if not call_id or not messages:
            return
        encoded = self.enc(messages)
        attrs: Dict[str, Any] = {
            "mas.llm.messages": encoded,
            semconv.GEN_AI_INPUT_MESSAGES: encoded,
            semconv.IOA_ENTITY_INPUT: encoded,
        }
        tokens = ev.get("total_tokens")
        if tokens not in (None, ""):
            attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] = int(tokens)
        self.annotate_open_span(call_id, attrs)
        meta = self._span_meta.get(self._lookup_span_key(call_id) or call_id) or {}
        if meta.get("boundary") == "LLMCall":
            meta["llm_messages"] = encoded

    def record_agent_output(self, call_id: str | None, text: str) -> None:
        """Remember the last user-facing output for an open AgentCall."""
        cleaned = str(text or "").strip()
        if not call_id or not cleaned:
            return
        self._agent_outputs[str(call_id)] = cleaned
        extra = {"mas.output": cleaned}
        if self._converter_profile == "observe_sdk":
            extra[semconv.IOA_ENTITY_OUTPUT] = cleaned
        self.annotate_open_span(call_id, extra)

    def agent_output_for(self, call_id: str | None) -> str:
        if not call_id:
            return ""
        return self._agent_outputs.get(str(call_id), "")

    def rewrite_delegate_tool_start(self, ev: Dict[str, Any]) -> bool:
        """Remap ``delegate_to_*`` — see :mod:`.delegation`."""
        return _rewrite_delegate_tool_start(self, ev)

    def rewrite_delegate_tool_end(self, ev: Dict[str, Any]) -> bool:
        """Remap ``delegate_to_*`` close — see :mod:`.delegation`."""
        return _rewrite_delegate_tool_end(self, ev)

    def ensure_resumed_agent_call(self, ev: Dict[str, Any]) -> str | None:
        """Open the next caller AgentCall after a rewritten delegation."""
        return _ensure_resumed_agent_call(self, ev)

    def resolve_agent_end_call_id(self, ev: Dict[str, Any]) -> str:
        """Map ``execution_end`` onto the live visit (original or resume)."""
        return _resolve_agent_end_call_id(self, ev)

    def point_span(
        self,
        name: str,
        attrs: Dict[str, Any],
        parent_call_id: str | None = None,
        ts_ns: int | None = None,
        call_id: str | None = None,
        *,
        attach_to_root: bool = False,
        events: list[tuple[str, Dict[str, Any]]] | None = None,
        reuse_span_id: int | None = None,
    ) -> int | None:
        """Open and immediately close a point-in-time span.

        ``events`` adds OTel span events (name, attributes) before the span
        closes -- e.g. the real SDK's ``agent_start_event``/``agent_end_event``
        spans each also carry a same-named nested event.

        ``reuse_span_id``, if given, makes the *new* span reuse that exact
        span id instead of a fresh random one (see ``emit_realtime_signal``
        and ``_SharedTraceIdGenerator.reuse_span_id`` in session.py) --
        requires an active ``id_generator`` that supports it; a no-op
        otherwise. Returns the span's own (int) span id either way, so a
        caller can pass it to a later, correlated point span.
        """
        if attrs.get("mas.boundary") == "CallAnnotation" and not self._annotation_enabled:
            return None
        seed = f"{name}|{call_id or ''}|{parent_call_id or ''}|{ts_ns or 0}|{sorted(attrs.items())}"
        span_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        point_attrs = dict(attrs)
        if call_id:
            point_attrs["mas.call.id"] = call_id
        if reuse_span_id is not None and isinstance(self._id_generator, SpanIdReuse):
            self._id_generator.reuse_span_id(reuse_span_id)
        # set_call_id=False → the synthetic span_key is never written as an
        # attribute (it would be a random, non-deterministic value).
        raw_span_id = self.open_span(
            span_key,
            name,
            point_attrs,
            parent_call_id,
            start_ns=ts_ns,
            set_call_id=False,
            attach_to_root=attach_to_root,
        )
        if events:
            opened = self._open_spans.get(span_key)
            if opened is not None:
                span, _ = opened
                for event_name, event_attrs in events:
                    # Explicit timestamp: otherwise add_event() stamps real
                    # wall-clock "now", which never matches between a live
                    # run and a replay of the same recorded events.
                    span.add_event(event_name, event_attrs, timestamp=ts_ns)
        self.close_span(span_key, end_ns=ts_ns)
        return raw_span_id

    def emit_session_start(self, ev: Dict[str, Any] | None = None) -> None:
        """Observe-sdk ``session.start`` bookend."""
        if self._session_start_emitted:
            return
        ts_ns = self.ts_ns(ev) if ev else self._shifted_ts_ns(self._earliest_event_ts())
        ts_s = (ts_ns / 1_000_000_000) if ts_ns is not None else 0.0
        self._ensure_trace_root(ts_ns)
        self.point_span(
            semconv.SPAN_NAME_SESSION_START,
            self._session_span_attrs(ts_s, ended=False),
            parent_call_id=(
                None
                if self._converter_profile == "observe_sdk"
                else self._TRACE_ROOT_CALL_ID
            ),
            ts_ns=ts_ns,
            attach_to_root=self._converter_profile == "observe_sdk",
        )
        self._session_start_emitted = True

    def emit_session_end(self) -> None:
        """Observe-sdk ``session.end`` bookend. Idempotent."""
        if self._session_end_emitted or not self._session_start_emitted:
            return
        ts_ns = self._shifted_ts_ns(self._session_end_event_ts())
        ts_s = (ts_ns / 1_000_000_000) if ts_ns is not None else 0.0
        attrs = self._session_span_attrs(ts_s, ended=True)
        self.point_span(
            semconv.SPAN_NAME_SESSION_END,
            attrs,
            parent_call_id=(
                None
                if self._converter_profile == "observe_sdk"
                else self._TRACE_ROOT_CALL_ID
            ),
            ts_ns=ts_ns,
            attach_to_root=self._converter_profile == "observe_sdk",
        )
        self._session_end_emitted = True

    def _should_emit_span(self, boundary: str) -> bool:
        """Observe-sdk default: only ingest categories (plus opt-in extras)."""
        if self._converter_profile != "observe_sdk":
            return True
        if boundary in self._OBSERVE_SDK_CORE_BOUNDARIES or boundary == "TraceRoot":
            return True
        if boundary == "CallAnnotation":
            return bool(self._annotation_enabled)
        if boundary == "GovernanceEvent":
            return bool(self._export_layers.governance)
        if boundary in self._OBSERVE_SDK_EXTENSION_BOUNDARIES:
            return bool(self._extensions)
        # Unknown PascalCase names (NetworkCall leftovers, generic kinds).
        return bool(self._extensions or self._annotation_enabled)

    def _observe_sdk_export_attrs(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Pass attributes through. OXP ingest silently ignores unknown keys.

        We used to drop ``mas.*``, ``session.name``, ``application.id``,
        ``tool_name``, ``gen_ai.completion.0.*``, and
        ``gen_ai.ioa.graph.protocol`` to match noa-trip-planner exactly.
        Extra attributes are harmless on ingest, so they stay on the wire.
        """
        return attrs

    def _session_span_attrs(self, ts_s: float, *, ended: bool) -> Dict[str, Any]:
        session_id = semconv.session_id_for(self._app_name, self._session_uuid)
        if self._converter_profile == "observe_sdk":
            # Mirrors the real SDK's own session.start/session.end shape
            # (verified against the installed ioa-observe-sdk): .start
            # carries application.id but no workflow name; .end carries
            # the workflow name but no application id; neither carries
            # mas.status/execution.success/session.name/entity.name.
            # norm's own get_success() already falls back to the span's
            # OTel StatusCode when execution.success is absent, so this
            # doesn't weaken OXP ingestion.
            attrs: Dict[str, Any] = {
                "mas.boundary": "Session",
                semconv.SESSION_ID: session_id,
                semconv.IOA_START_TIME: str(ts_s),
                semconv.APPLICATION_ID: self._app_name,
            }
            if ended:
                attrs[semconv.IOA_WORKFLOW_NAME] = self._app_name
                attrs["session.ended_at"] = str(ts_s)
            else:
                attrs["application.id"] = self._app_name
                attrs["session.started_at"] = str(ts_s)
            return attrs
        attrs = {
            "mas.boundary": "Session",
            semconv.APPLICATION_ID: self._app_name,
            "application.id": self._app_name,
            semconv.SESSION_NAME: self._app_name,
            semconv.SESSION_ID: session_id,
            semconv.IOA_START_TIME: str(ts_s),
            semconv.IOA_WORKFLOW_NAME: self._app_name,
            semconv.IOA_ENTITY_NAME: self._app_name or "session",
            semconv.EXECUTION_SUCCESS: "true",
        }
        if ended:
            attrs["session.ended_at"] = str(ts_s)
        else:
            attrs["session.started_at"] = str(ts_s)
        return attrs

    def _earliest_event_ts(self) -> float | None:
        times = [
            float(event["timestamp"])
            for event in self._seen_events
            if isinstance(event.get("timestamp"), (int, float))
        ]
        return min(times) if times else None

    def _latest_event_ts(self) -> float | None:
        times = [
            float(event["timestamp"])
            for event in self._seen_events
            if isinstance(event.get("timestamp"), (int, float))
        ]
        return max(times) if times else None

    def _session_end_event_ts(self) -> float | None:
        """Bookend the session on the MAS run / last agent, not a later context event."""
        if self._mas_call_end_ts is not None:
            return self._mas_call_end_ts
        if self._last_closed_agent_end_ns is not None:
            return self._last_closed_agent_end_ns / 1_000_000_000 - self._timestamp_offset_s
        return self._latest_event_ts()

    def emit_graph_span(
        self,
        topology: Dict[str, Any],
        *,
        app_name: str = "",
        ts_ns: int | None = None,
        parent_call_id: str | None = None,
        protocol: str = "MAS",
    ) -> None:
        """Emit the multi-agent ``<app>.graph`` topology span (OXP-required).

        A span of kind ``graph`` carrying ``gen_ai.ioa.graph`` (+ dynamism /
        determinism), matching the IOA Observe ``@graph`` contract.  See
        :mod:`mas.library.telemetry.conversion.topology`.

        Realtime mode replaces this retrospective span with incremental
        ``topology.node.*`` signals, so this is a no-op when ``realtime`` is on.
        """
        if self._realtime:
            return
        from mas.library.telemetry.conversion.topology import (
            graph_span_attributes,
            has_topology,
            require_mas_name,
        )

        if not has_topology(topology):
            return
        app = require_mas_name(app_name, self._app_name)
        name = f"{app}.{semconv.SPAN_SUFFIX_GRAPH}"
        attrs = {
            "mas.boundary": "Graph",
            **graph_span_attributes(topology, protocol=protocol),
            semconv.IOA_ENTITY_NAME: app,
            semconv.IOA_WORKFLOW_NAME: app,
            semconv.EXECUTION_SUCCESS: "true",
        }
        self._ensure_trace_root(ts_ns)
        observe = self._converter_profile == "observe_sdk"
        self.point_span(
            name,
            attrs,
            None if observe else (parent_call_id or self._TRACE_ROOT_CALL_ID),
            ts_ns=ts_ns,
            attach_to_root=observe,
        )

    def emit_realtime_signal(self, ev: Dict[str, Any]) -> None:
        """Emit incremental observe-sdk realtime categories (opt-in).

        norm's realtime dispatch (``handlers/{tool,chat}.py``) requires the
        same ``ioa_observe.entity.name`` / ``ioa_observe.agent.span_id``
        fields a batch ``.tool``/``.chat`` span carries -- without them it
        silently drops the signal (``entity_name`` empty -> early return).
        A tool/LLM *_end event's own record never repeats the tool name or
        model, so the ``*_start`` call's own tracked meta is the fallback.
        """
        kind = str(ev.get("kind") or "")
        agent = self.agent_id(ev)
        mapping = {
            "execution_start": "topology.node.started",
            "execution_end": "topology.node.completed",
            "tool_call_start": "tool.started",
            "tool_call_end": "tool.completed",
            "llm_call_start": "llm.started",
            "llm_call_end": "llm.completed",
        }
        name = mapping.get(kind)
        if not name:
            return
        call_id = str(ev.get("call_id") or "")
        attrs = {
            "mas.boundary": "RealtimeSignal",
            "event.name": name,
            semconv.AGENT_ID: agent,
            "mas.agent.id": agent,
        }
        if kind == "tool_call_start":
            entity_name = str(ev.get("tool_name") or agent)
            self._realtime_entity_names[call_id] = entity_name
            attrs[semconv.IOA_ENTITY_NAME] = entity_name
        elif kind == "llm_call_start":
            entity_name = str(ev.get("model") or "") or agent
            self._realtime_entity_names[call_id] = entity_name
            attrs[semconv.IOA_ENTITY_NAME] = entity_name
        elif kind in ("tool_call_end", "llm_call_end"):
            attrs[semconv.IOA_ENTITY_NAME] = self._realtime_entity_names.pop(call_id, agent)
        agent_span = self._agent_span_ids.get(agent)
        if agent_span:
            attrs[semconv.IOA_AGENT_SPAN_ID] = semconv.wire_span_id(agent_span)
        if self._app_name:
            attrs[semconv.APPLICATION_ID] = self._app_name
            if self._session_uuid:
                attrs[semconv.SESSION_ID] = semconv.session_id_for(
                    self._app_name, self._session_uuid
                )
        reuse_id = self._realtime_span_ids.pop(call_id, None) if kind.endswith("_end") else None
        raw_span_id = self.point_span(
            name,
            attrs,
            ev.get("parent_call_id") or ev.get("call_id"),
            ts_ns=self.ts_ns(ev),
            reuse_span_id=reuse_id,
        )
        if kind.endswith("_start") and raw_span_id is not None:
            self._realtime_span_ids[call_id] = raw_span_id

    def emit_duplicate_start_annotation(self, ev: Dict[str, Any], kind: str) -> bool:
        """Mirror native KG: duplicate ``*_start`` while call open → CallAnnotation."""
        if not ev.get("_duplicate_start_annotation"):
            call_id = ev.get("call_id")
            if not call_id or call_id not in self._open_spans:
                return False
        logger.warning(
            "duplicate_call_start: kind=%s call_id=%s",
            kind,
            ev.get("call_id"),
        )
        self.point_span(
            "CallAnnotation",
            {
                "mas.boundary": "CallAnnotation",
                "mas.agent.id": self.agent_id(ev),
                "mas.annotation.kind": kind,
            },
            ev.get("parent_call_id"),
            ts_ns=self.ts_ns(ev),
            call_id=ev.get("call_id"),
        )
        return True

    # -- small stateless helpers used by handlers ----------------------

    def ts_ns(self, ev: Dict[str, Any]) -> Optional[int]:
        """Convert event timestamp (float seconds) to nanoseconds int."""
        ts = ev.get("timestamp")
        if not isinstance(ts, (int, float)):
            return None
        return self._shifted_ts_ns(float(ts))

    def _shifted_ts_ns(self, ts: float | None) -> Optional[int]:
        if ts is None:
            return None
        return int((float(ts) + self._timestamp_offset_s) * 1_000_000_000)

    @staticmethod
    def agent_id(ev: Dict[str, Any]) -> str:
        """Extract agent_id from an event, defaulting to 'unknown'."""
        return str(ev.get("agent_id") or "unknown")

    @staticmethod
    def require_call_id(ev: Dict[str, Any]) -> str:
        """Return event call_id, generating a fresh UUID when absent."""
        return str(ev.get("call_id") or uuid.uuid4())

    def span_key(self, ev: Dict[str, Any]) -> str:
        """Tracking key for an interval span.

        Structural calls own the native ``call_id``. Wrapper/annotation
        intervals nest under ``{call_id}:{kind}`` so they do not steal the
        LLM/tool/agent slot when the runtime reuses the same id.
        """
        cid = self._stable_call_id(ev)
        kind = str(ev.get("kind") or "")
        if kind.endswith("_end"):
            kind = kind[: -len("_end")] + "_start"
        if kind in self._STRUCTURAL_START_KINDS:
            return cid
        return f"{cid}:{kind}"

    _GENERIC_AGENT_IDS: ClassVar[frozenset[str]] = frozenset(
        {"", "unknown", "agent", "mas"}
    )

    def _stable_call_id(self, ev: Dict[str, Any]) -> str:
        """Call id that start/end share even when the native event left it blank."""
        cid = str(ev.get("call_id") or "").strip()
        if cid:
            return cid
        parent = str(ev.get("parent_call_id") or "")
        agent = str(ev.get("agent_id") or "")
        extra = str(
            ev.get("processing_type")
            or ev.get("processing_name")
            or ev.get("part_id")
            or ev.get("kind")
            or "anon"
        )
        return f"{agent}:{parent}:{extra}"

    _STRUCTURAL_START_KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "mas_call_start",
            "execution_start",
            "llm_call_start",
            "tool_call_start",
            "processing_call_start",
            "memory_call_start",
            "memory_store_start",
            "memory_read_start",
            "memory_retrieve_start",
            "rag_query_start",
            "skill_execution_start",
            "network_call_start",
            "workflow_transition_start",
            "agent_communication_start",
        }
    )

    @staticmethod
    def enc(value: Any, limit: int | None = None) -> str:
        """Encode an arbitrary value to a JSON string. Pass ``limit`` to cap the length; by default nothing is cut."""
        if value is None:
            return ""
        try:
            text = json.dumps(value, ensure_ascii=True, default=str)
        except Exception:  # pragma: no cover - defensive
            text = str(value)
        return text if limit is None else text[:limit]

    def is_open(self, call_id: str | None) -> bool:
        return bool(call_id) and call_id in self._open_spans

    def is_closed(self, call_id: str | None) -> bool:
        return bool(call_id) and str(call_id) in self._closed_call_ids

    # ------------------------------------------------------------------
    # Internal: parent context + call-id scoping
    # ------------------------------------------------------------------

    def _lookup_span_key(self, call_id: str | None, agent_id: str = "") -> str | None:
        """Resolve a native/scoped call id onto an open or closed span key."""
        if not call_id:
            return None
        cid = str(call_id)
        candidates = [cid]
        if agent_id and not cid.startswith(f"{agent_id}-"):
            candidates.append(f"{agent_id}-{cid}")
        for key in candidates:
            if key in self._open_spans or key in self._closed_span_ctx:
                return key
        for store in (self._open_spans, self._closed_span_ctx):
            for key in store:
                if key.endswith(f"-{cid}") or key.endswith(cid) or cid in key.split(":"):
                    return key
        return None

    def _open_agent_call_id(self, agent_id: str = "") -> str | None:
        """The live ``AgentCall`` for *agent_id*, or None (never another agent)."""
        if not agent_id or agent_id in self._GENERIC_AGENT_IDS:
            return None
        for call_id in reversed(list(self._span_meta)):
            if call_id not in self._open_spans:
                continue
            meta = self._span_meta.get(call_id) or {}
            if meta.get("boundary") == "AgentCall" and meta.get("agent") == agent_id:
                return call_id
        return None

    def _fallback_parent_call_id(self, agent_id: str = "") -> str | None:
        """Prefer the current AgentCall for *agent_id*, else the innermost span.

        A named agent must not inherit another agent's open ``AgentCall``.
        Trip-planner interleaves the specialist's execution with the
        moderator's post-delegate reply; stealing that parent made the
        second moderator visit disappear.
        """
        mine = self._open_agent_call_id(agent_id)
        if mine:
            return mine
        if agent_id and agent_id not in self._GENERIC_AGENT_IDS:
            return None
        for call_id in reversed(list(self._span_meta)):
            if call_id not in self._open_spans:
                continue
            meta = self._span_meta.get(call_id) or {}
            if meta.get("boundary") == "AgentCall" and (
                not agent_id
                or agent_id in self._GENERIC_AGENT_IDS
                or meta.get("agent") == agent_id
            ):
                return call_id
        for call_id in reversed(list(self._span_meta)):
            if call_id not in self._open_spans:
                continue
            if (self._span_meta.get(call_id) or {}).get("boundary") == "AgentCall":
                return call_id
        if self._open_spans:
            return next(reversed(self._open_spans))
        return None

    def _effective_parent_call_id(
        self,
        parent_call_id: str | None,
        agent_id: str = "",
        *,
        boundary: str = "",
    ) -> str | None:
        """Use an explicit parent if it exists, else the current AgentCall.

        Native events name parents that are not yet open (interleaved
        files, remapped ``{agent}-{id}`` keys, delegate tool ids). Returning
        that raw id made ``_parent_ctx`` emit a root sibling, which is why
        the Observe span tree went flat.

        Observe-sdk / OXP: ``*.chat`` and ``*.tool`` nest under the live
        ``*.agent``. LangGraph (and norm) treat delegated ``*.agent`` spans
        as siblings under ``invoke_agent``, linked by
        ``ioa_observe.agent.previous`` — not as children of the caller.
        """
        resolved = (
            self._lookup_span_key(str(parent_call_id), agent_id)
            if parent_call_id
            else None
        )
        if parent_call_id and str(parent_call_id) in self._rewritten_delegate_ids:
            resolved = None
        if resolved and resolved in self._rewritten_delegate_ids:
            resolved = None
        if self._converter_profile == "observe_sdk" and boundary in {
            "LLMCall",
            "ToolCall",
        }:
            agent_parent = self._open_agent_call_id(agent_id) or self._fallback_parent_call_id(
                agent_id
            )
            if agent_parent:
                return agent_parent
        if self._converter_profile == "observe_sdk" and boundary == "AgentCall":
            return self._open_boundary_call_id("TaskCall")
        if resolved:
            return resolved
        fallback = self._fallback_parent_call_id(agent_id)
        if fallback:
            return fallback
        if self._converter_profile != "observe_sdk":
            # Raw profile: nothing else to attach to — fall back to the
            # synthetic trace root so every span shares one TraceId and
            # exactly one span ends up parentless (see _ensure_trace_root).
            return self._TRACE_ROOT_CALL_ID
        return None

    def _open_boundary_call_id(self, boundary: str) -> str | None:
        for call_id in reversed(list(self._span_meta)):
            if call_id not in self._open_spans:
                continue
            if (self._span_meta.get(call_id) or {}).get("boundary") == boundary:
                return call_id
        return None

    def _inherit_agent_id(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Native ``context_part_contributed`` often sets ``agent_id=agent``."""
        agent = str(event.get("agent_id") or "")
        if agent not in self._GENERIC_AGENT_IDS:
            return event
        parent = self._lookup_span_key(str(event.get("parent_call_id") or ""), agent)
        inherited = ""
        if parent:
            inherited = str((self._span_meta.get(parent) or {}).get("agent") or "")
        if not inherited or inherited in self._GENERIC_AGENT_IDS:
            fallback = self._fallback_parent_call_id(agent)
            if fallback:
                inherited = str(
                    (self._span_meta.get(fallback) or {}).get("agent") or ""
                )
        if inherited and inherited not in self._GENERIC_AGENT_IDS:
            scoped = dict(event)
            scoped["agent_id"] = inherited
            return scoped
        return event

    def _parent_ctx(self, parent_call_id: str | None) -> Any:
        """Return OTel context with the parent span set, or the root context."""
        if parent_call_id and parent_call_id in self._open_spans:
            parent_span, _ = self._open_spans[parent_call_id]
            return trace.set_span_in_context(parent_span)
        if parent_call_id and parent_call_id in self._closed_span_ctx:
            parent = NonRecordingSpan(self._closed_span_ctx[parent_call_id])
            return trace.set_span_in_context(parent)
        if self._converter_profile == "observe_sdk":
            return self._observe_root_context()
        return context_api.Context()

    def _scope_call_id(self, call_id: str | None, agent_id: str) -> str | None:
        """Disambiguate shared call ids (legacy ``u1-exec``, cross-agent reuse)."""
        if not call_id or not agent_id:
            return call_id
        cid = str(call_id)
        if cid.endswith("-exec") and not cid.startswith(f"{agent_id}-"):
            return f"{agent_id}-{cid}"
        owner = self._call_id_agents.get(cid)
        if owner and owner != agent_id:
            return f"{agent_id}-{cid}"
        self._call_id_agents[cid] = agent_id
        return cid

    def _resolve_scoped_id(self, call_id: str, agent_id: str) -> str:
        """Apply agent ownership prefix for shared legacy ids (``*-exec``)."""
        prefixed = f"{agent_id}-{call_id}" if agent_id and not call_id.startswith(f"{agent_id}-") else ""
        if prefixed and (
            prefixed in self._open_spans or prefixed in self._closed_call_ids
        ):
            return prefixed
        owner = self._call_id_agents.get(call_id)
        if owner:
            if call_id.endswith("-exec") and not call_id.startswith(f"{owner}-"):
                return f"{owner}-{call_id}"
            return call_id
        scoped = self._scope_call_id(call_id, agent_id)
        return scoped if scoped is not None else call_id

    def _scope_event_call_ids(self, event: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(event.get("agent_id") or "")
        if not agent_id:
            return event
        scoped = dict(event)
        kind = str(scoped.get("kind") or "")
        is_end = kind.endswith("_end") or kind == "user_response"
        if scoped.get("call_id") is not None:
            if is_end:
                cid = scoped.get("call_id")
                scoped["call_id"] = (
                    self._resolve_scoped_id(str(cid), agent_id) if cid else cid
                )
            else:
                scoped["call_id"] = self._scope_call_id(scoped.get("call_id"), agent_id)
                cid = str(scoped["call_id"])
                if kind.endswith("_start") and cid in self._open_spans:
                    scoped["_duplicate_start_annotation"] = True
                elif cid in self._open_spans:
                    scoped["call_id"] = f"{agent_id}-{cid}"
                    self._call_id_agents[str(scoped["call_id"])] = agent_id
        if scoped.get("parent_call_id") is not None:
            pcid = scoped.get("parent_call_id")
            scoped["parent_call_id"] = (
                self._resolve_scoped_id(str(pcid), agent_id) if pcid else pcid
            )
        if (
            scoped.get("call_id")
            and scoped.get("parent_call_id")
            and str(scoped["call_id"]) == str(scoped["parent_call_id"])
        ):
            scoped["parent_call_id"] = None
        return scoped

    def _profile_span_name(self, name: str, attrs: Dict[str, Any]) -> str:
        if self._converter_profile != "observe_sdk":
            return name
        if name in {semconv.SPAN_NAME_SESSION_START, semconv.SPAN_NAME_SESSION_END}:
            return name
        boundary = str(attrs.get("mas.boundary") or "")
        agent = str(attrs.get("mas.agent.id") or "unknown")
        if name.endswith(f".{semconv.SPAN_SUFFIX_GRAPH}") or boundary == "Graph":
            return name
        if boundary == "TaskCall":
            return semconv.invoke_agent_span_name(self._app_name)
        if boundary == "AgentCall":
            return f"{agent}.{semconv.SPAN_SUFFIX_AGENT}"
        if boundary == "LLMCall":
            return f"{agent}.{semconv.SPAN_SUFFIX_CHAT}"
        if boundary == "ToolCall":
            tool_name = str(attrs.get("mas.tool.name") or "tool")
            return f"{tool_name}.{semconv.SPAN_SUFFIX_TOOL}"
        if boundary == "CallAnnotation":
            kind = str(attrs.get("mas.annotation.kind") or "annotation")
            if kind in {"routing", "routing_result"}:
                kind = "routing_annotation"
            return f"{agent}.{kind}"
        if self._extensions:
            if boundary == "MemoryCall":
                mem = str(attrs.get("mas.memory.type") or agent or "memory")
                return f"{mem}.{semconv.SPAN_SUFFIX_MEMORY}"
            if boundary == "ProcessingCall":
                actor = str(attrs.get("mas.processing.actor") or agent or "processing")
                return f"{actor}.{semconv.SPAN_SUFFIX_PROCESSING}"
            if boundary == "ContextContribution":
                return f"{agent}.{semconv.SPAN_SUFFIX_CONTEXT}"
            if boundary == "GovernanceEvent":
                return f"{agent}.{semconv.SPAN_SUFFIX_GOVERNANCE}"
            if boundary in {"SkillCall", "SkillExecution"}:
                skill = str(attrs.get("mas.skill.name") or attrs.get("mas.tool.name") or "skill")
                return f"{skill}.{semconv.SPAN_SUFFIX_SKILL}"
            if boundary == "RAGQuery":
                return f"{agent}.{semconv.SPAN_SUFFIX_RAG}"
        return name

    def _apply_profile_overlay_on_open(
        self, attrs: Dict[str, Any], start_ns: int | None
    ) -> Dict[str, Any]:
        if self._converter_profile != "observe_sdk":
            return attrs
        out = dict(attrs)
        boundary = str(out.get("mas.boundary") or "")
        kind_map = {
            "TaskCall": "task",
            "AgentCall": "agent",
            "LLMCall": "llm",
            "ToolCall": "tool",
            "CallAnnotation": "annotation",
            "ProcessingCall": "processing",
            "ContextContribution": "context",
            "MemoryCall": "memory",
            "GovernanceEvent": "governance",
            "SkillCall": "skill",
            "SkillExecution": "skill",
            "RAGQuery": "rag",
        }
        if boundary in kind_map:
            out.setdefault(semconv.IOA_SPAN_KIND, kind_map[boundary])
        if start_ns is not None:
            out.setdefault(semconv.IOA_START_TIME, str(start_ns / 1_000_000_000))
        out.setdefault(semconv.EXECUTION_SUCCESS, "true")
        if self._app_name:
            out.setdefault(semconv.IOA_WORKFLOW_NAME, self._app_name)

        agent = str(out.get("mas.agent.id") or "")
        call_id = str(out.get("mas.call.id") or "")
        if agent:
            out.setdefault(semconv.AGENT_ID, agent)
        if boundary in {"LLMCall", "ToolCall"} and agent:
            parent_span = self._agent_span_ids.get(agent)
            if parent_span:
                out.setdefault(semconv.IOA_AGENT_SPAN_ID, semconv.wire_span_id(parent_span))
            parent_trace = self._agent_trace_ids.get(agent)
            if parent_trace:
                out.setdefault(semconv.IOA_AGENT_TRACE_ID, semconv.wire_trace_id(parent_trace))

        if boundary == "AgentCall":
            value = out.get("mas.input")
            out.setdefault(
                semconv.IOA_ENTITY_INPUT,
                self._timeline_io(
                    "input", agent or "agent", call_id, value, wrap=True
                ),
            )
            out.setdefault(semconv.IOA_ENTITY_NAME, agent or "agent")
            pending = self._pending_delegations.pop(agent, None) if agent else None
            # Caller agent_id from the intercepted delegate_to_<id> tool
            # (or the previously opened AgentCall). Never a hardcoded name.
            previous = (
                str(pending.get("caller") or "")
                if pending
                else self._last_agent_id
            )
            if previous and previous != agent:
                out.setdefault(semconv.IOA_AGENT_PREVIOUS, previous)
            self._agent_sequence += 1
            out.setdefault(semconv.IOA_AGENT_SEQUENCE, str(self._agent_sequence))
            if self._last_agent_span_id:
                out.setdefault(
                    semconv.IOA_HANDOFF_SOURCE_SPAN_IDS,
                    json.dumps([semconv.wire_span_id(self._last_agent_span_id)]),
                )
                if self._last_agent_trace_id:
                    out.setdefault(
                        semconv.IOA_HANDOFF_SOURCE_TRACE_IDS,
                        json.dumps([semconv.wire_trace_id(self._last_agent_trace_id)]),
                    )

        if boundary == "LLMCall":
            messages = out.get("mas.llm.messages")
            llm_input = self._timeline_io(
                "llm-input", agent or "llm", call_id, messages
            )
            out.setdefault(semconv.IOA_ENTITY_INPUT, llm_input)
            provider, model = semconv.provider_and_model(str(out.get("mas.llm.model") or ""))
            out.setdefault(semconv.GEN_AI_PROVIDER, provider)
            out.setdefault(semconv.GEN_AI_REQUEST_MODEL, model)
            out.setdefault(semconv.IOA_ENTITY_NAME, model)
            out.setdefault(semconv.GEN_AI_OPERATION_NAME, "chat")
            temp = out.get("mas.llm.temperature")
            if temp is not None:
                out.setdefault(semconv.GEN_AI_REQUEST_TEMPERATURE, float(temp))
            first = self._first_llm_message(messages)
            out.setdefault(semconv.GEN_AI_INPUT_MESSAGES, str(messages) if messages else llm_input)
            if first:
                role = first.get("role")
                content = first.get("content")
                if role:
                    out.setdefault(semconv.GEN_AI_PROMPT_ROLE, str(role))
                if content:
                    out.setdefault(semconv.GEN_AI_PROMPT_CONTENT, str(content))

        if boundary == "ToolCall":
            tool_name = out.get("mas.tool.name")
            if tool_name:
                out.setdefault("tool_name", str(tool_name))
                out.setdefault(semconv.IOA_ENTITY_NAME, str(tool_name))
            value = out.get("mas.tool.input")
            tool_input = self._timeline_io(
                "tool-input", str(tool_name or agent or "tool"), call_id, value
            )
            out.setdefault(semconv.IOA_ENTITY_INPUT, tool_input)
            out.setdefault(semconv.GEN_AI_TOOL_ARGUMENTS, str(value) if value else tool_input)

        if self._extensions and boundary == "MemoryCall":
            out.setdefault(
                semconv.OBSERVE_MEMORY_OPERATION,
                str(out.get("mas.memory.operation") or ""),
            )
            out.setdefault(semconv.OBSERVE_MEMORY_TYPE, str(out.get("mas.memory.type") or ""))
            out.setdefault(
                semconv.IOA_ENTITY_NAME,
                str(out.get("mas.memory.type") or agent or "memory"),
            )
        if self._extensions and boundary == "ProcessingCall":
            out.setdefault(semconv.OBSERVE_PROCESSING_ACTOR, agent)
            out.setdefault(semconv.IOA_ENTITY_NAME, agent or "processing")
        if self._extensions and boundary == "GovernanceEvent":
            out.setdefault(
                semconv.OBSERVE_GOVERNANCE_DECISION_TYPE,
                str(out.get("mas.governance.decision") or out.get("mas.annotation.kind") or ""),
            )
            out.setdefault(semconv.IOA_ENTITY_NAME, agent or "governance")

        return out

    def _apply_profile_overlay_on_close(
        self,
        span: Any,
        meta: Dict[str, Any],
        extra: Dict[str, Any],
        *,
        status: str = "success",
        call_id: str = "",
        end_ns: int | None = None,
    ) -> None:
        if self._converter_profile != "observe_sdk":
            return
        failed = str(status or "").strip().lower() in {"error", "failed", "failure"}
        span.set_attribute(semconv.EXECUTION_SUCCESS, "false" if failed else "true")
        boundary = str(meta.get("boundary") or "")
        if boundary == "AgentCall":
            value = extra.get("mas.output") or self._span_attr(span, semconv.IOA_ENTITY_OUTPUT)
            agent = str(meta.get("agent") or "agent")
            # Always wrap: if Agent output equals the last child output,
            # OXP skips the capability-chain-boundary and Inspect parks
            # the Agent bar after the call row.
            text = self._timeline_io(
                "output",
                agent,
                call_id,
                value,
                wrap=True,
            )
            span.set_attribute(semconv.IOA_ENTITY_OUTPUT, text)
            self.point_span(
                semconv.SPAN_NAME_AGENT_END_EVENT,
                {"mas.boundary": "AgentLifecycleEvent", "agent_id": agent},
                parent_call_id=call_id,
                ts_ns=end_ns,
                events=[
                    (
                        semconv.SPAN_NAME_AGENT_END_EVENT,
                        {"agent_name": agent, "type": "agent"},
                    )
                ],
            )
            return
        if boundary == "LLMCall":
            value = extra.get("mas.llm.response")
            text = str(value).strip() if value else self._span_attr(span, semconv.GEN_AI_OUTPUT_MESSAGES)
            if not text:
                agent = str(meta.get("agent") or "llm")
                text = self._timeline_io("llm-output", agent, call_id, "")
            span.set_attribute(semconv.IOA_ENTITY_OUTPUT, text)
            span.set_attribute(semconv.GEN_AI_COMPLETION_CONTENT, text)
            span.set_attribute(semconv.GEN_AI_COMPLETION_ROLE, "assistant")
            span.set_attribute(semconv.GEN_AI_OUTPUT_MESSAGES, text)
            finish = extra.get("mas.llm.finish_reason")
            if finish:
                span.set_attribute(semconv.GEN_AI_FINISH_REASONS, str(finish))
            input_tokens = extra.get("metrics.token.input")
            output_tokens = extra.get("metrics.token.output")
            total_tokens = extra.get("metrics.token.total")
            if input_tokens is not None:
                span.set_attribute(semconv.GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
            if output_tokens is not None:
                span.set_attribute(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
            if total_tokens is not None:
                span.set_attribute(semconv.GEN_AI_USAGE_TOTAL_TOKENS, int(total_tokens))
            return
        if boundary == "ToolCall":
            value = extra.get("mas.tool.output")
            text = str(value).strip() if value else self._span_attr(span, semconv.GEN_AI_TOOL_RESULT)
            if not text:
                tool = str(meta.get("tool_name") or meta.get("agent") or "tool")
                text = self._timeline_io("tool-output", tool, call_id, "")
            span.set_attribute(semconv.IOA_ENTITY_OUTPUT, text)
            span.set_attribute(semconv.GEN_AI_TOOL_RESULT, text)

    @staticmethod
    def _timeline_io(
        kind: str,
        identity: str,
        call_id: str,
        value: Any,
        *,
        wrap: bool = False,
    ) -> str:
        """Return real I/O when present, else a unique placeholder.

        OXP ``bridge_states_if_mismatched`` is a no-op when neighbouring
        State contents are equal. Cached traces often leave every output
        blank, so every state becomes ``(no content recorded)``, capability-
        chain-boundary bridges are skipped, and Inspect parks Session/MAS/
        Agent bars after the call row. A per-span fallback keeps contents
        distinct so the path stays connected. ``wrap=True`` keeps the real
        text but prefixes it so an Agent's I/O cannot equal a child's.
        """
        text = str(value or "").strip()
        if wrap:
            return f"{kind}:{identity or 'span'}:{text or call_id or 'anon'}"
        if text:
            return text
        return f"{kind}:{identity or 'span'}:{call_id or 'anon'}"

    @staticmethod
    def _span_attr(span: Any, key: str) -> str:
        try:
            raw = (span.attributes or {}).get(key)
        except Exception:  # pragma: no cover
            return ""
        return str(raw).strip() if raw else ""

    @staticmethod
    def _first_llm_message(raw: Any) -> Dict[str, Any] | None:
        if not raw:
            return None
        payload = raw
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return None


__all__ = ["MasOtelConverter", "SpanIdReuse"]
