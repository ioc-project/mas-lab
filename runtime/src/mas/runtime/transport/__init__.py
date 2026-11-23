#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Transport observability primitives. No OpenTelemetry, no protocol names."""

from mas.runtime.schema.transport import (
    DISCOVERY,
    ERROR,
    INTERNAL,
    MESSAGE_COMPLETED,
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    SESSION,
    TRANSPORT_KINDS,
    TransportEvent,
)
from mas.runtime.transport.classifier import (
    Alias,
    AliasTable,
    Decision,
    Drop,
    Emit,
    Passthrough,
    SpanView,
    TransportClassifier,
)
from mas.runtime.transport.config import (
    ProtocolTransportConfig,
    TransportObservabilityConfig,
)
from mas.runtime.transport.emit import (
    bind_config,
    bind_sink,
    emit_transport,
    is_enabled,
    protocol_config,
    unbind_sink,
)

__all__ = [
    "DISCOVERY",
    "ERROR",
    "INTERNAL",
    "MESSAGE_COMPLETED",
    "MESSAGE_RECEIVED",
    "MESSAGE_SENT",
    "SESSION",
    "TRANSPORT_KINDS",
    "Alias",
    "AliasTable",
    "Decision",
    "Drop",
    "Emit",
    "Passthrough",
    "ProtocolTransportConfig",
    "SpanView",
    "TransportClassifier",
    "TransportEvent",
    "TransportObservabilityConfig",
    "bind_config",
    "bind_sink",
    "emit_transport",
    "is_enabled",
    "protocol_config",
    "unbind_sink",
]
