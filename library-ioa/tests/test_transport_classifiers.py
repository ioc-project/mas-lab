#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Replay inventory span fixtures through protocol classifiers."""

from __future__ import annotations

import json
from pathlib import Path

from library_ioa.plugins.a2a.classifier import A2ATransportClassifier
from library_ioa.plugins.mcp.aliases import alias_table as mcp_alias_table
from library_ioa.plugins.mcp.classifier import MCPTransportClassifier
from library_ioa.plugins.slim.aliases import alias_table as slim_alias_table
from library_ioa.plugins.slim.classifier import SlimTransportClassifier
from mas.runtime.schema.transport import INTERNAL, SESSION
from mas.runtime.transport.classifier import Drop, Emit, Passthrough, SpanView

FIXTURES = Path(__file__).resolve().parents[2] / "library-telemetry" / "tests" / "transport" / "fixtures"


def _views(path: Path) -> list[SpanView]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    views = []
    for span in payload.get("spans") or []:
        views.append(
            SpanView(
                name=str(span.get("name") or ""),
                scope_name=str(span.get("scope_name") or ""),
                scope_version=str(span.get("scope_version") or ""),
                kind=str(span.get("kind") or ""),
                attributes=dict(span.get("attributes") or {}),
                span_id=str(span.get("span_id") or ""),
                parent_span_id=str(span.get("parent_span_id") or ""),
                trace_id=str(span.get("trace_id") or ""),
                status=str(span.get("status") or ""),
            )
        )
    return views


def test_a2a_message_spans_are_dropped() -> None:
    classifier = A2ATransportClassifier()
    path = FIXTURES / "a2a" / "1.1.5" / "blocking_message.json"
    decisions = [classifier.classify_end(view) for view in _views(path)]
    message_decisions = [
        d
        for view, d in zip(_views(path), decisions, strict=True)
        if "send_message" in view.name or "on_message_send" in view.name
    ]
    assert message_decisions
    assert all(isinstance(d, Drop) for d in message_decisions)
    internals = [d for d in decisions if isinstance(d, Emit)]
    assert internals
    assert all(d.event_fields.get("kind") == INTERNAL for d in internals)


def test_mcp_tools_call_spans_are_dropped() -> None:
    classifier = MCPTransportClassifier()
    path = FIXTURES / "mcp" / "2.2.0" / "tools_call_success.json"
    for view in _views(path):
        decision = classifier.classify_end(view)
        assert isinstance(decision, Drop)


def _view(**fields: object) -> SpanView:
    payload = dict(
        name="",
        scope_name="",
        scope_version="",
        kind="",
        attributes={},
        span_id="1",
        parent_span_id="",
        trace_id="1",
        status="",
    )
    payload.update(fields)
    return SpanView(**payload)  # type: ignore[arg-type]


def test_classifiers_advertise_scope_range_and_aliases() -> None:
    a2a = A2ATransportClassifier()
    assert a2a.scope_names() == ("a2a-python-sdk",)
    assert a2a.supported_sdk_range()
    assert a2a.mapped_span_names()
    assert a2a.aliases().events
    assert isinstance(a2a.classify_end(_view(name="other", scope_name="not-a2a")), Passthrough)

    mcp = MCPTransportClassifier()
    assert mcp.scope_names() == ("mcp-python-sdk",)
    assert mcp.supported_sdk_range()
    assert mcp.mapped_span_names() == ()
    assert mcp.aliases() is mcp_alias_table()
    hooked = _view(attributes={"mcp.method.name": "tools/call"})
    assert isinstance(mcp.classify_start(hooked), Drop)
    assert isinstance(mcp.classify_start(_view()), Passthrough)
    init = mcp.classify_end(_view(attributes={"mcp.method.name": "initialize"}))
    assert isinstance(init, Emit) and init.event_fields["kind"] == SESSION
    assert isinstance(mcp.classify_end(_view(name="other", scope_name="not-mcp")), Passthrough)

    slim = SlimTransportClassifier()
    assert slim.scope_names() == ()
    assert slim.supported_sdk_range()
    assert slim.mapped_span_names() == ()
    assert slim.aliases() is slim_alias_table()
    assert isinstance(slim.classify_start(_view()), Drop)
    assert isinstance(slim.classify_end(_view()), Drop)


def test_sdk_span_never_emits_message_kind() -> None:
    """Invariant 2: a converted SDK span never creates a message.* event."""
    classifier = A2ATransportClassifier()
    path = FIXTURES / "a2a" / "1.1.5" / "blocking_message.json"
    for view in _views(path):
        for decision in (classifier.classify_start(view), classifier.classify_end(view)):
            if isinstance(decision, Emit):
                assert not str(decision.event_fields.get("kind") or "").startswith("transport.message")
