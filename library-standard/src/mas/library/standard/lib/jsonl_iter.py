#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Streaming JSONL readers — line-at-a-time, never ``read_text`` the whole file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def peek_jsonl(path: Path | str) -> dict[str, Any] | None:
    """Return the first JSON object in *path*, or ``None`` if the file is empty."""
    dest = Path(path)
    if not dest.is_file():
        return None
    with dest.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
    return None


def iter_jsonl_records(
    path: Path | str,
    *,
    batch_size: int = 256,
) -> Iterator[list[dict[str, Any]]]:
    """Yield batches of JSON objects from a JSONL file.

    Opens the file once and parses line by line. Suitable for large traces
    that must not be loaded as a single string.
    """
    dest = Path(path)
    size = max(1, int(batch_size))
    batch: list[dict[str, Any]] = []
    with dest.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                batch.append(parsed)
            if len(batch) >= size:
                yield batch
                batch = []
    if batch:
        yield batch
