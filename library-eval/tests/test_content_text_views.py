#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""List content is read as text in eval providers."""

from __future__ import annotations

from mas.library.eval.mce.trace_provider import MASTraceProvider
from mas.library.eval.providers.trip_planner_gt import TripPlannerGTProvider


def test_trip_planner_extracts_text_from_assistant_part_list() -> None:
    provider = TripPlannerGTProvider()
    kg = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "the fare is 12"},
                    {
                        "type": "image",
                        "media_type": "image/png",
                        "sha256": "abc",
                        "name": "chart.png",
                        "bytes": 4,
                    },
                ],
            }
        ]
    }
    assert provider._extract_output(kg) == "the fare is 12\n[image chart.png]"


def test_trace_provider_reads_list_content_and_mas_call_parent() -> None:
    provider = MASTraceProvider.__new__(MASTraceProvider)
    events = [
        {"kind": "mas_call_start", "call_id": "mas-1"},
        {
            "kind": "execution_start",
            "call_id": "ex-1",
            "parent_call_id": "mas-1",
            "agent_id": "clerk",
            "input": "see the chart",
        },
        {
            "kind": "tool_call_start",
            "call_id": "t-1",
            "agent_id": "clerk",
            "tool_name": "lookup",
            "arguments": {"id": 1},
        },
        {
            "kind": "user_response",
            "agent_id": "clerk",
            "content": [{"type": "text", "text": "up and to the right"}],
        },
    ]
    assert provider._detect_root_agent(events) == "clerk"
    assert provider._extract_query(events) == "see the chart"
    spans = provider._extract_tool_spans(events)
    assert spans[0]["toolArguments"] == '{"id": 1}'
    text = provider._build_conversation(events)
    assert "up and to the right" in text
