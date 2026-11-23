#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native transport fields to the observe-sdk names SCOPE's normalizer reads."""

from __future__ import annotations

from mas.runtime.schema.transport import DISCOVERY, MESSAGE_COMPLETED, MESSAGE_RECEIVED, MESSAGE_SENT
from mas.runtime.transport.classifier import Alias, AliasTable

ALIASES = AliasTable(
    events={
        MESSAGE_SENT: Alias(
            event="a2a.message.sent",
            attributes={"source": "source.agent", "target": "target.agent", "message_id": "message_id"},
        ),
        MESSAGE_RECEIVED: Alias(
            event="a2a.message.received",
            attributes={"source": "source.agent", "target": "target.agent", "message_id": "message_id"},
        ),
        MESSAGE_COMPLETED: Alias(
            event="a2a.message.completed",
            attributes={"source": "source.agent", "target": "target.agent", "message_id": "message_id"},
        ),
        DISCOVERY: Alias(
            event="a2a.agent_card",
            attributes={"card": "capability.content"},
        ),
    }
)


__all__ = ["ALIASES"]
