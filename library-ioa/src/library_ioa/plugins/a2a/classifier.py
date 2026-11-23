#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A SDK span classifier. Message meaning comes from hooks; spans are internals."""

from __future__ import annotations

from mas.runtime.schema.transport import INTERNAL
from mas.runtime.transport.classifier import (
    AliasTable,
    Decision,
    Drop,
    Emit,
    Passthrough,
    SpanView,
)

from library_ioa.plugins.a2a.aliases import ALIASES

SCOPE = "a2a-python-sdk"
SUPPORTED_RANGE = ">=1.1.5,<1.2"

_MESSAGE_MARKERS = (
    "send_message",
    "on_message_send",
    "_handle_send_message",
    "send_message_streaming",
    "on_message_send_stream",
)
_MAPPED = (
    "a2a.client.transports.jsonrpc.JsonRpcTransport.send_message",
    "a2a.client.transports.jsonrpc.JsonRpcTransport._send_request",
    "a2a.server.request_handlers.default_request_handler_v2.DefaultRequestHandlerV2.on_message_send",
    "a2a.server.events.event_queue_v2.EventQueueSource.enqueue_event",
    "a2a.server.events.event_queue_v2.EventQueueSource.dequeue_event",
)


def _is_message(name: str) -> bool:
    return any(marker in name for marker in _MESSAGE_MARKERS)


class A2ATransportClassifier:
    """Hooks emit message events. SDK spans of the same call are dropped."""

    protocol = "a2a"
    sdk_distribution = "a2a-sdk"

    def scope_names(self) -> tuple[str, ...]:
        return (SCOPE,)

    def supported_sdk_range(self) -> str:
        return SUPPORTED_RANGE

    def mapped_span_names(self) -> tuple[str, ...]:
        return _MAPPED

    def aliases(self) -> AliasTable:
        return ALIASES

    def classify_start(self, span: SpanView) -> Decision:
        # Announce once, at the end, when the attributes are complete.
        return Drop()

    def classify_end(self, span: SpanView) -> Decision:
        if _is_message(span.name):
            return Drop()
        if span.scope_name != SCOPE:
            return Passthrough()
        return Emit(
            {
                "kind": INTERNAL,
                "protocol": "a2a",
                "side": "server" if span.kind == "SERVER" else "client",
                "correlation_key": span.span_id,
                "attributes": {"span_name": span.name},
            }
        )


__all__ = ["A2ATransportClassifier", "SCOPE", "SUPPORTED_RANGE"]
