#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``context_assembled`` copies archive_view_id onto the span, not the prompt."""

from __future__ import annotations

import json
from typing import Any

from mas.library.telemetry.conversion.mappings.context import h_context_assembled


class _Conv:
    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any]]] = []
        self.attached: dict[str, Any] | None = None

    def attach_assembled_llm_context(self, ev: dict[str, Any]) -> None:
        self.attached = ev

    def agent_id(self, ev: dict[str, Any]) -> str:
        return str(ev.get("agent_id") or "unknown")

    def ts_ns(self, ev: dict[str, Any]) -> int:
        _ = ev
        return 0

    def point_span(
        self,
        name: str,
        attrs: dict[str, Any],
        parent_call_id: str | None,
        ts_ns: int | None = None,
    ) -> None:
        _ = parent_call_id, ts_ns
        self.spans.append((name, attrs))


def test_archive_view_id_is_span_attribute_not_message_body() -> None:
    conv = _Conv()
    h_context_assembled(
        conv,
        {
            "agent_id": "planner",
            "archive_view_id": "view-abc",
            "segments": [{"tokens": 4}],
            "parent_call_id": "llm-1",
        },
    )
    assert conv.attached is not None
    assert conv.spans
    _name, attrs = conv.spans[0]
    assert attrs["mas.archive.view_id"] == "view-abc"
    annotation = json.loads(attrs["mas.annotation.content"])
    assert annotation["archive_view_id"] == "view-abc"
    assert annotation["segments"] == 1
    assert annotation["total_tokens"] == 4


def test_missing_archive_view_id_omits_the_attribute() -> None:
    conv = _Conv()
    h_context_assembled(conv, {"agent_id": "planner", "segments": 2, "total_tokens": 9})
    _name, attrs = conv.spans[0]
    assert "mas.archive.view_id" not in attrs
    annotation = json.loads(attrs["mas.annotation.content"])
    assert "archive_view_id" not in annotation
    assert annotation == {"segments": 2, "total_tokens": 9}
