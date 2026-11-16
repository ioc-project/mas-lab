#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TrajectoryExtractor reads the LLM call as the runtime writes it.

``llm_call_start`` is the request (``model``, ``messages``); ``llm_call_end``
is the return (``output``). Reading ``response`` only gave an empty output for
every runtime trace.
"""

from __future__ import annotations

import json

from mas.lab.dataset.extractor import TrajectoryExtractor


def _write(tmp_path, events: list[dict]):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _events(end: dict) -> list[dict]:
    return [
        {
            "kind": "llm_call_start",
            "agent_id": "planner",
            "model": "bedrock/m",
            "messages": [{"role": "user", "content": "renew"}],
        },
        {"kind": "llm_call_end", "agent_id": "planner", "latency_ms": 12.0, **end},
    ]


def test_call_record_takes_model_from_start_and_output_from_end(tmp_path) -> None:
    record = TrajectoryExtractor(_write(tmp_path, _events({"output": "done"}))).extract()
    (call,) = record["calls"]
    assert call["model"] == "bedrock/m"
    assert call["output"] == "done"
    assert call["input_prompt"] == "renew"
    assert call["latency_ms"] == 12.0


def test_agent_level_output_falls_back_to_the_llm_output(tmp_path) -> None:
    record = TrajectoryExtractor(_write(tmp_path, _events({"output": "done"}))).extract()
    assert record["agents"]["planner"]["final_output"] == "done"


def test_an_openai_shaped_response_is_still_read_when_output_is_empty(tmp_path) -> None:
    events = _events({"output": "", "response": {"content": "from response"}})
    record = TrajectoryExtractor(_write(tmp_path, events)).extract()
    assert record["calls"][0]["output"] == "from response"


def test_a_response_without_content_gives_empty_text_not_the_word_none(tmp_path) -> None:
    events = _events({"output": "", "response": {"content": None}})
    record = TrajectoryExtractor(_write(tmp_path, events)).extract()
    assert record["calls"][0]["output"] == ""


def test_agent_output_is_the_reply_not_the_empty_text_of_a_tool_use_turn(tmp_path) -> None:
    """The last model call of an agent that then replied asked for a tool: its text is empty."""
    events = [
        *_events({"output": ""}),
        {"kind": "user_response", "agent_id": "planner", "content": "Take the 9:40 train."},
        {"kind": "execution_end", "agent_id": "planner"},
    ]
    record = TrajectoryExtractor(_write(tmp_path, events)).extract()
    assert record["agents"]["planner"]["final_output"] == "Take the 9:40 train."
