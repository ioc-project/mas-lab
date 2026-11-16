#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The delegation view reads an agent's reply from ``output``, as the runtime writes it."""

from __future__ import annotations

from mas.lab.plots.trajectory.extract import _extract_agent_output


def test_agent_output_is_read_from_the_output_field() -> None:
    events = [
        {"kind": "llm_call_end", "agent_id": "a", "timestamp": 2.0, "output": "the planner reply has four words"},
    ]
    assert _extract_agent_output(events, "a", 1.0, 3.0) == "the planner reply has four words"


def test_agent_output_still_reads_an_openai_shaped_response() -> None:
    events = [
        {"kind": "llm_call_end", "agent_id": "a", "timestamp": 2.0, "response": {"content": "the old shape has words"}},
    ]
    assert _extract_agent_output(events, "a", 1.0, 3.0) == "the old shape has words"
