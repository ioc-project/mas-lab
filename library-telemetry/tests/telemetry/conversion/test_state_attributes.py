#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``ioa_observe.state.*`` — the native explicit dataflow, copied onto spans.

The native events name each element's input and output State. The converter
copies those fields and nothing else: no placeholder, no ``input:`` wrapper,
no id it had to infer.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.replay import replay_events_file

IN_ID = "ioa_observe.state.input.id"
IN_CONTENT = "ioa_observe.state.input.content"
OUT_ID = "ioa_observe.state.output.id"
OUT_CONTENT = "ioa_observe.state.output.content"
PROCESSING_NAME = "ioa_observe.processing.name"

# A real trip-planner run in the explicit format (delegation, parallel groups), committed
# with the library-kg path-equivalence fixtures.
REAL_TRACE = (
    Path(__file__).resolve().parents[4]
    / "library-kg"
    / "tests"
    / "fixtures"
    / "path_equivalence"
    / "explicit-events.jsonl.gz"
)


def _real_events() -> list[dict]:
    with gzip.open(REAL_TRACE, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


_PLACEHOLDER_PREFIXES = ("input:", "output:", "llm-input:", "llm-output:", "tool-input:", "tool-output:")


def _replay(tmp_path: Path, events: list[dict], profile: str = "observe_sdk") -> list[dict]:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="demo", converter_profile=profile)
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]


def _attrs(span: dict) -> dict:
    return span.get("attributes") or {}


def _processing(spans: list[dict], name: str) -> list[dict]:
    return sorted(
        (s for s in spans if _attrs(s).get(PROCESSING_NAME) == name),
        key=lambda s: s["start_time"],
    )


def _named(spans: list[dict], name: str) -> dict:
    matches = [s for s in spans if s["name"] == name]
    assert len(matches) == 1, [s["name"] for s in spans]
    return matches[0]


def _ids(raw: str) -> list[str]:
    """A State id attribute: one id, or the JSON list of a join's member ids."""
    return json.loads(raw) if raw.startswith("[") else [raw]


# ---------------------------------------------------------------------------
# Single agent: agent, context assembly, LLM tool-use turn, tool, context, LLM.
# ---------------------------------------------------------------------------

_SYS = '[{"content":"sys","role":"system"}]'
_SYS_TOOL = '[{"content":"sys","role":"system"},{"content":"tool","role":"tool"}]'
_TOOL_CALLS = '[{"arguments":{"q":"x"},"toolName":"lookup"}]'


def _single_agent_events() -> list[dict]:
    return [
        {"kind": "mas_call_start", "call_id": "run", "agent_id": "mas", "timestamp": 1.0, "session_id": "s1"},
        {
            "kind": "execution_start",
            "call_id": "a-exec",
            "parent_call_id": "run",
            "agent_id": "agent",
            "timestamp": 1.1,
            "input": "hi",
            "state_out": "a-exec#0",
            "state_out_content": "hi",
        },
        {
            "kind": "processing_call_start",
            "call_id": "ctx-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.2,
            "processing_name": "context assembly",
            "processing_type": "context_assembly",
            "state_in": "a-exec#0",
            "state_in_content": "hi",
            "state_out": "a-exec#1",
        },
        {
            "kind": "processing_call_end",
            "call_id": "ctx-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.2,
            "processing_name": "context assembly",
            "processing_type": "context_assembly",
            "state_in": "a-exec#0",
            "state_in_content": "hi",
            "state_out": "a-exec#1",
            "state_out_content": _SYS,
        },
        {
            "kind": "llm_call_start",
            "call_id": "llm-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.3,
            "model": "openai/gpt-4o",
            "messages": [{"role": "system", "content": "sys"}],
            "state_in": "a-exec#1",
            "state_in_content": _SYS,
            "state_out": "a-exec#2",
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.4,
            "output": "",
            "tool_calls": [{"toolName": "lookup", "arguments": {"q": "x"}}],
            "state_out_content": _TOOL_CALLS,
        },
        {
            "kind": "tool_call_start",
            "call_id": "tool-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.5,
            "tool_name": "lookup",
            "arguments": {"q": "x"},
            "state_in": "a-exec#2",
            "state_in_content": _TOOL_CALLS,
            "state_out": "a-exec#3",
        },
        {
            "kind": "tool_call_end",
            "call_id": "tool-1",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.6,
            "output": '{"found":1}',
            "state_out_content": '{"found":1}',
        },
        {
            "kind": "processing_call_start",
            "call_id": "ctx-2",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.7,
            "processing_name": "context assembly",
            "processing_type": "context_assembly",
            "state_in": "a-exec#3",
            "state_in_content": '{"found":1}',
            "state_out": "a-exec#4",
        },
        {
            "kind": "processing_call_end",
            "call_id": "ctx-2",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.7,
            "processing_name": "context assembly",
            "processing_type": "context_assembly",
            "state_in": "a-exec#3",
            "state_in_content": '{"found":1}',
            "state_out": "a-exec#4",
            "state_out_content": _SYS_TOOL,
        },
        {
            "kind": "llm_call_start",
            "call_id": "llm-2",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.8,
            "model": "openai/gpt-4o",
            "messages": [{"role": "tool", "content": "tool"}],
            "state_in": "a-exec#4",
            "state_in_content": _SYS_TOOL,
            "state_out": "a-exec#5",
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm-2",
            "parent_call_id": "a-exec",
            "agent_id": "agent",
            "timestamp": 1.9,
            "output": "done",
            "state_out_content": "done",
        },
        {
            "kind": "execution_end",
            "call_id": "a-exec",
            "parent_call_id": "run",
            "agent_id": "agent",
            "timestamp": 2.0,
            "status": "ok",
            "state_in": "a-exec#5",
            "state_in_content": "done",
        },
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "agent_id": "mas",
            "timestamp": 2.1,
            "status": "success",
            "state_in": "a-exec#0",
            "state_in_content": "hi",
            "state_out": "a-exec#5",
            "state_out_content": "done",
        },
    ]


def test_single_agent_chain_links_every_element(tmp_path):
    spans = _replay(tmp_path, _single_agent_events())
    agent = _named(spans, "agent.agent")
    contexts = _processing(spans, "context assembly")
    chats = sorted((s for s in spans if s["name"] == "agent.chat"), key=lambda s: s["start_time"])
    # Elements in the order the visit runs them: context, LLM turn, tool, context, LLM.
    elements = [contexts[0], chats[0], _named(spans, "lookup.tool"), contexts[1], chats[1]]
    # Every span carries the four attributes, with the contract's values.
    for span in [agent, *elements]:
        assert IN_ID in _attrs(span), span["name"]
        assert OUT_ID in _attrs(span), span["name"]
        assert IN_CONTENT in _attrs(span), span["name"]
    assert _attrs(agent)[IN_ID] == "a-exec#0"
    assert _attrs(agent)[IN_CONTENT] == "hi"
    assert _attrs(agent)[OUT_ID] == "a-exec#5"
    assert _attrs(agent)[OUT_CONTENT] == "done"

    # Each element reads the State the previous element wrote.
    prev_id, prev_content = _attrs(agent)[IN_ID], _attrs(agent)[IN_CONTENT]
    for span in elements:
        assert _attrs(span)[IN_ID] == prev_id, span["name"]
        assert _attrs(span)[IN_CONTENT] == prev_content, span["name"]
        assert OUT_CONTENT in _attrs(span), span["name"]
        prev_id, prev_content = _attrs(span)[OUT_ID], _attrs(span)[OUT_CONTENT]
    assert prev_id == _attrs(agent)[OUT_ID]
    assert prev_content == _attrs(agent)[OUT_CONTENT]


def test_single_agent_session_end_carries_the_boundary_states(tmp_path):
    spans = _replay(tmp_path, _single_agent_events())
    session_end = _named(spans, semconv.SPAN_NAME_SESSION_END)
    attrs = _attrs(session_end)
    assert attrs[IN_ID] == "a-exec#0"
    assert attrs[IN_CONTENT] == "hi"
    assert attrs[OUT_ID] == "a-exec#5"
    assert attrs[OUT_CONTENT] == "done"


def test_raw_profile_carries_no_state_attributes(tmp_path):
    spans = _replay(tmp_path, _single_agent_events(), profile="raw")
    assert not any(k.startswith("ioa_observe.state.") for s in spans for k in _attrs(s))


def test_no_span_has_a_wrapper_or_placeholder(tmp_path):
    spans = _replay(tmp_path, _single_agent_events())
    for span in spans:
        for key, value in _attrs(span).items():
            if isinstance(value, str):
                assert not value.startswith(_PLACEHOLDER_PREFIXES), (span["name"], key, value[:80])
                assert "(no content" not in value, (span["name"], key)


# ---------------------------------------------------------------------------
# Parallel group: members read the group input; the aggregate joins them.
# ---------------------------------------------------------------------------


def test_parallel_group_aggregate_joins_member_states(tmp_path):
    events = [
        {"kind": "mas_call_start", "call_id": "run", "agent_id": "mas", "timestamp": 1.0},
        {
            "kind": "execution_start",
            "call_id": "m-exec",
            "parent_call_id": "run",
            "agent_id": "m",
            "timestamp": 1.1,
            "input": "plan",
            "state_out": "m-exec#0",
            "state_out_content": "plan",
        },
        {
            "kind": "parallel_group_start",
            "call_id": "pg",
            "parent_call_id": "m-exec",
            "agent_id": "m",
            "timestamp": 1.2,
            "group_id": "g1",
        },
        {
            "kind": "tool_call_start",
            "call_id": "t1",
            "parent_call_id": "m-exec",
            "agent_id": "m",
            "timestamp": 1.3,
            "tool_name": "lookup_a",
            "arguments": {},
            "state_in": "m-exec#0",
            "state_in_content": "plan",
            "state_out": "m-exec#1",
        },
        {
            "kind": "tool_call_start",
            "call_id": "t2",
            "parent_call_id": "m-exec",
            "agent_id": "m",
            "timestamp": 1.3,
            "tool_name": "lookup_b",
            "arguments": {},
            "state_in": "m-exec#0",
            "state_in_content": "plan",
            "state_out": "m-exec#2",
        },
        {
            "kind": "tool_call_end",
            "call_id": "t1",
            "agent_id": "m",
            "timestamp": 1.4,
            "output": "A",
            "state_out_content": "A",
        },
        {
            "kind": "tool_call_end",
            "call_id": "t2",
            "agent_id": "m",
            "timestamp": 1.4,
            "output": "B",
            "state_out_content": "B",
        },
        {"kind": "parallel_group_end", "call_id": "pg", "parent_call_id": "m-exec", "agent_id": "m", "timestamp": 1.5},
        {
            "kind": "processing_call_start",
            "call_id": "agg",
            "parent_call_id": "m-exec",
            "agent_id": "m",
            "timestamp": 1.5,
            "processing_name": "aggregate",
            "processing_type": "parallel_group",
            "state_in": ["m-exec#1", "m-exec#2"],
            "state_in_content": '["A","B"]',
            "state_out": "m-exec#3",
        },
        {
            "kind": "processing_call_end",
            "call_id": "agg",
            "parent_call_id": "m-exec",
            "agent_id": "m",
            "timestamp": 1.5,
            "processing_name": "aggregate",
            "processing_type": "parallel_group",
            "state_in": ["m-exec#1", "m-exec#2"],
            "state_in_content": '["A","B"]',
            "state_out": "m-exec#3",
            "state_out_content": '["A","B"]',
        },
        {
            "kind": "execution_end",
            "call_id": "m-exec",
            "parent_call_id": "run",
            "agent_id": "m",
            "timestamp": 1.6,
            "status": "ok",
            "state_in": "m-exec#3",
            "state_in_content": '["A","B"]',
        },
        {"kind": "mas_call_end", "call_id": "run", "agent_id": "mas", "timestamp": 1.7, "status": "success"},
    ]
    spans = _replay(tmp_path, events)
    (join,) = _processing(spans, "aggregate")
    ids = json.loads(_attrs(join)[IN_ID])
    contents = json.loads(_attrs(join)[IN_CONTENT])
    assert ids == ["m-exec#1", "m-exec#2"]
    assert contents == ["A", "B"]
    assert len(ids) == len(contents)
    assert _attrs(join)[OUT_ID] == "m-exec#3"
    assert _attrs(join)[OUT_CONTENT] == '["A","B"]'
    # The agent's output is the join State.
    agent = _named(spans, "m.agent")
    assert _attrs(agent)[OUT_ID] == "m-exec#3"


# ---------------------------------------------------------------------------
# Delegation: caller tool, delegate processing, callee agent, reply processing.
# ---------------------------------------------------------------------------


def test_delegation_joins_caller_tool_and_callee_visit(tmp_path):
    tool_input = '[{"arguments":{"task":"x"},"toolName":"delegate_to_schedule"}]'
    events = [
        {"kind": "mas_call_start", "call_id": "run", "agent_id": "mas", "timestamp": 1.0},
        {
            "kind": "execution_start",
            "call_id": "m-exec",
            "parent_call_id": "run",
            "agent_id": "moderator",
            "timestamp": 1.1,
            "input": "plan",
            "state_out": "m-exec#0",
            "state_out_content": "plan",
        },
        {
            "kind": "tool_call_start",
            "call_id": "d-tool",
            "parent_call_id": "m-exec",
            "agent_id": "moderator",
            "timestamp": 1.2,
            "tool_name": "delegate_to_schedule",
            "arguments": {"task": "x"},
            "state_in": "m-exec#0",
            "state_in_content": tool_input,
            "state_out": "m-exec#1",
        },
        {
            "kind": "processing_call_start",
            "call_id": "d-tool:delegate",
            "parent_call_id": "m-exec",
            "agent_id": "moderator",
            "timestamp": 1.25,
            "processing_name": "delegate",
            "processing_type": "agent_boundary",
            "state_in": "m-exec#0",
            "state_in_content": tool_input,
            "state_out": "sched-exec#0",
            "state_out_content": "x",
        },
        {
            "kind": "processing_call_end",
            "call_id": "d-tool:delegate",
            "parent_call_id": "m-exec",
            "agent_id": "moderator",
            "timestamp": 1.25,
            "processing_name": "delegate",
            "processing_type": "agent_boundary",
            "state_in": "m-exec#0",
            "state_in_content": tool_input,
            "state_out": "sched-exec#0",
            "state_out_content": "x",
        },
        {
            "kind": "execution_start",
            "call_id": "sched-exec",
            "parent_call_id": "d-tool",
            "agent_id": "schedule_agent",
            "timestamp": 1.3,
            "input": "x",
            "state_out": "sched-exec#0",
            "state_out_content": "x",
        },
        {
            "kind": "llm_call_start",
            "call_id": "s-llm",
            "parent_call_id": "sched-exec",
            "agent_id": "schedule_agent",
            "timestamp": 1.4,
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
            "state_in": "sched-exec#0",
            "state_in_content": "x",
            "state_out": "sched-exec#1",
        },
        {
            "kind": "llm_call_end",
            "call_id": "s-llm",
            "parent_call_id": "sched-exec",
            "agent_id": "schedule_agent",
            "timestamp": 1.5,
            "output": "result",
            "state_out_content": "result",
        },
        {
            "kind": "execution_end",
            "call_id": "sched-exec",
            "agent_id": "schedule_agent",
            "timestamp": 1.6,
            "status": "ok",
            "state_in": "sched-exec#1",
            "state_in_content": "result",
        },
        {
            "kind": "processing_call_start",
            "call_id": "d-tool:reply",
            "parent_call_id": "m-exec",
            "agent_id": "moderator",
            "timestamp": 1.65,
            "processing_name": "reply",
            "processing_type": "agent_boundary",
            "state_in": "sched-exec#1",
            "state_in_content": "result",
            "state_out": "m-exec#1",
            "state_out_content": "result",
        },
        {
            "kind": "processing_call_end",
            "call_id": "d-tool:reply",
            "parent_call_id": "m-exec",
            "agent_id": "moderator",
            "timestamp": 1.65,
            "processing_name": "reply",
            "processing_type": "agent_boundary",
            "state_in": "sched-exec#1",
            "state_in_content": "result",
            "state_out": "m-exec#1",
            "state_out_content": "result",
        },
        {
            "kind": "tool_call_end",
            "call_id": "d-tool",
            "agent_id": "moderator",
            "timestamp": 1.7,
            "output": "result",
            "state_out_content": "result",
        },
        {
            "kind": "execution_end",
            "call_id": "m-exec",
            "parent_call_id": "run",
            "agent_id": "moderator",
            "timestamp": 1.8,
            "status": "ok",
            "state_in": "m-exec#1",
            "state_in_content": "result",
        },
        {"kind": "mas_call_end", "call_id": "run", "agent_id": "mas", "timestamp": 1.9, "status": "success"},
    ]
    spans = _replay(tmp_path, events)
    delegate = _processing(spans, "delegate")[0]
    reply = _processing(spans, "reply")[0]
    tool = _named(spans, "delegate_to_schedule.tool")
    callee = _named(spans, "schedule_agent.agent")
    caller = [s for s in spans if s["name"] == "moderator.agent"]
    assert len(caller) == 1, "a delegating turn is one caller visit"

    # The processing pairs carry the processing name, like other processing spans.
    assert _attrs(delegate)[PROCESSING_NAME] == "delegate"
    assert _attrs(reply)[PROCESSING_NAME] == "reply"

    # Delegate hands the tool's input to the callee's first State.
    assert _attrs(delegate)[OUT_ID] == _attrs(callee)[IN_ID] == "sched-exec#0"
    assert _attrs(delegate)[IN_ID] == _attrs(tool)[IN_ID] == "m-exec#0"
    # Reply hands the callee's final State back as the tool's output.
    assert _attrs(reply)[IN_ID] == _attrs(callee)[OUT_ID] == "sched-exec#1"
    assert _attrs(reply)[OUT_ID] == _attrs(tool)[OUT_ID] == "m-exec#1"
    assert _attrs(tool)[OUT_CONTENT] == "result"


# ---------------------------------------------------------------------------
# The real explicit-format trace.
# ---------------------------------------------------------------------------


def test_real_trace_every_input_state_is_produced(tmp_path):
    spans = _replay(tmp_path, _real_events())
    produced: set[str] = set()
    consumed: set[str] = set()
    for span in spans:
        attrs = _attrs(span)
        if OUT_ID in attrs:
            produced.update(_ids(attrs[OUT_ID]))
        if IN_ID in attrs:
            consumed.update(_ids(attrs[IN_ID]))
        if span["name"].endswith(".agent"):
            # An agent visit's first State is written by execution_start, which
            # has no span of its own: the visit's input is its producer.
            produced.update(_ids(attrs[IN_ID]))
    assert consumed
    assert consumed <= produced, sorted(consumed - produced)[:10]
