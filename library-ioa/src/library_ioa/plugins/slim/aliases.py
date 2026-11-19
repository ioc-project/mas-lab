#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SLIM native fields to observe-sdk names."""

from __future__ import annotations

from mas.runtime.schema.transport import MESSAGE_RECEIVED, MESSAGE_SENT
from mas.runtime.transport.classifier import Alias, AliasTable

ALIASES = AliasTable(
    events={
        MESSAGE_SENT: Alias(
            event="slim.message.sent",
            attributes={"source": "source.agent", "target": "target.agent"},
        ),
        MESSAGE_RECEIVED: Alias(
            event="slim.message.received",
            attributes={"source": "source.agent", "target": "target.agent"},
        ),
    }
)


def alias_table() -> AliasTable:
    return ALIASES


__all__ = ["ALIASES", "alias_table"]
