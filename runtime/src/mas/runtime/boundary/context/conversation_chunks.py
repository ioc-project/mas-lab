#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Conversation archive (compat import).

Implementation lives in ``mas.runtime.boundary.context.archive``.
"""

from mas.runtime.boundary.context.archive import (
    ConversationChunkStore,
    MessageChunk,
    SummaryChunk,
    new_chunk_id,
)

# Historical name used by tests and snapshots.
_new_chunk_id = new_chunk_id

__all__ = [
    "ConversationChunkStore",
    "MessageChunk",
    "SummaryChunk",
    "_new_chunk_id",
]
