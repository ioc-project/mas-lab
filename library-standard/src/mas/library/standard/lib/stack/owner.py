#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Who a conversation belongs to, written into its id.

A caller names a conversation with an id of its own choosing (an A2A ``contextId``). A server that keeps state per
conversation must not let two callers meet in one: the id a handler receives is therefore derived from the verified
owner and the id the caller sent, ``own.<owner>.<digest>``. Nothing is looked up and nothing expires: the same pair
always gives the same id, on any process, and a caller's id is always put behind the caller's own verified owner.

The derivation uses no secret. Anyone may compute the id of a pair; that does no harm because no wire field is ever read
as an internal id.
"""

from __future__ import annotations

import hashlib
import json

#: What an owner key starts with (``owner_key`` of an identity layer: ``own:`` and 40 hex digits).
OWNER_PREFIX = "own:"
_OWNER_LENGTH = len(OWNER_PREFIX) + 40


def conversation_id(owner: str, client_id: str) -> str:
    """The id a handler gets for the conversation that the verified ``owner`` names ``client_id``.

    Only ever computed from an owner a verifier produced, never from one a caller sent.
    """
    if not owner.startswith(OWNER_PREFIX) or len(owner) != _OWNER_LENGTH:
        raise ValueError("not an owner key")
    blob = json.dumps(["mas.conversation.v1", owner, client_id], separators=(",", ":"), ensure_ascii=True)
    return f"own.{owner[len(OWNER_PREFIX) :]}.{hashlib.sha256(blob.encode('ascii')).hexdigest()[:32]}"
