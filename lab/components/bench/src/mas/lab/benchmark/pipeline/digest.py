#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical JSON and rolling SHA-256 for stream / artifact records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Stable encoding: key-sorted JSON, shortest round-trip floats, no spaces."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rolling_digest(previous_hex: str, record: Any) -> str:
    """sha256(previous_digest_bytes ‖ canonical(record)); first record uses empty prev."""
    h = hashlib.sha256()
    if previous_hex:
        h.update(bytes.fromhex(previous_hex))
    h.update(canonical_bytes(record))
    return h.hexdigest()


def node_key(*parts: Any) -> str:
    """Content-addressed node key: sha256 of canonical parts."""
    return sha256_hex(canonical_bytes(list(parts)))
