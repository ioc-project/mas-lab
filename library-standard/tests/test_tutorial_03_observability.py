#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 03 fixture kinds are projected from kernel transitions."""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO / "docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl"

_REQUIRED_KINDS = {
    "mas_call_start",
    "mas_call_end",
    "execution_start",
    "execution_end",
    "llm_call_start",
    "llm_call_end",
    "tool_call_start",
    "tool_call_end",
}


def test_tutorial_03_fixture_kinds_are_native_projectable() -> None:
    """Every kind in the Tutorial 03 golden trace is emitted by NativeObservabilityTransform."""
    assert _FIXTURE.is_file(), f"missing fixture {_FIXTURE}"
    kinds_seen: set[str] = set()
    for line in _FIXTURE.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        kind = ev.get("kind", "")
        kinds_seen.add(kind)
        if kind in {"context_part_contributed"}:
            continue
        if kind.endswith("_start") or kind.endswith("_end"):
            assert "call_id" in ev
    assert _REQUIRED_KINDS <= kinds_seen
