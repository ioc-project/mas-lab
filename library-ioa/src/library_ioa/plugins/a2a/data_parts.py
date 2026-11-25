#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The data parts of a received A2A message, handed to a verifier.

A scheme whose answer is a data part on the same task (a handshake answer, a presentation) cannot read it from the
text, the headers or the message metadata. Both inbound paths (HTTP and slimrpc) therefore extract the message's data
parts and put them in ``Inbound.meta`` under one reserved key, :data:`DATA_PARTS_KEY`: a list, in message order, of the
JSON objects the parts held. The transport sets the key; one a caller sent in its own message metadata is dropped, so a
caller cannot write what looks like the transport's reading.

Limits: at most :data:`MAX_DATA_PARTS` parts, each a JSON object of at most :data:`MAX_DATA_PART_BYTES` bytes
serialized.
A message beyond them is refused before the verifier runs, with the value-free code :data:`REFUSED_CODE`; nothing of the
part is in the refusal. A message with no data part has no key.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

#: The ``Inbound.meta`` key (it contains ``/``, as every meta key does) that holds the received data parts.
DATA_PARTS_KEY = "mas.a2a/data_parts"
MAX_DATA_PARTS = 8
MAX_DATA_PART_BYTES = 16384
#: The code a message whose data parts break the limits is refused with.
REFUSED_CODE = "refused:data_part"


class DataPartError(ValueError):
    """A data part is not a JSON object, is too large, or there are too many. The text is the code, nothing else."""

    def __init__(self) -> None:
        super().__init__(REFUSED_CODE)


def data_parts(values: Iterable[Any]) -> list[dict[str, Any]]:
    """The data parts' values (as the transport parsed them) checked against the limits. ``DataPartError`` if broken."""
    found = list(values)
    if len(found) > MAX_DATA_PARTS:
        raise DataPartError
    for value in found:
        if not isinstance(value, dict):
            raise DataPartError
        try:
            size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
        except (TypeError, ValueError):
            raise DataPartError from None
        if size > MAX_DATA_PART_BYTES:
            raise DataPartError
    return found


def inbound_metadata(message_metadata: Mapping[str, Any], values: Iterable[Any]) -> dict[str, Any]:
    """What a verifier gets as ``Inbound.meta``: the caller's namespaced message metadata, and the data parts.

    Only keys with ``/`` of the caller's metadata are kept (caller data, never identity), and the reserved key is the
    transport's: a caller's own is removed. ``DataPartError`` when the parts break the limits.
    """
    meta = {k: v for k, v in message_metadata.items() if "/" in k and k != DATA_PARTS_KEY}
    parts = data_parts(values)
    if parts:
        meta[DATA_PARTS_KEY] = parts
    return meta
