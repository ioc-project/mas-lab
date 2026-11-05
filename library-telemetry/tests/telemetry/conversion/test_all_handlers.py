#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every registered event kind is converted without error (handler coverage).

Feeds one synthetic event of each registered ``kind`` through the converter
(all export layers on) and asserts spans are produced and no handler raises —
exercising every per-category handler in ``conversion/mappings/``.
"""

from __future__ import annotations

import json

from mas.library.telemetry.conversion.mappings.base import registered_kinds

from tests.conftest import requires_otel

# Minimal field bag covering the union of what handlers read (unused keys are
# ignored by handlers that don't need them).
_FIELDS = {
    "agent_id": "planner",
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "hi"}],
    "response": {
        "content": "ok",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    },
    "tool_name": "search",
    "arguments": {"q": "x"},
    "result": "done",
    "output": "done",
    "input": "start",
    "data": "note",
    "query": "q",
    "key": "k",
    "collection": "c",
    "top_k": 3,
    "segments": 3,  # processing uses int; context_assembled accepts int|list
    "total_tokens": 12,
    "memory_type": "episodic",
    "skill_name": "s",
    "url": "http://x",
    "method": "GET",
    "status_code": 200,
    "transition_type": "t",
    "processing_name": "p",
    "processing_type": "pt",
    "message_type": "m",
    "source_agent_id": "planner",
    "target_agent_id": "worker",
    "selected_agent": "worker",
    "routing_type": "handoff",
    "state_key": "sk",
    "state_value": "sv",
    "part_id": "pp",
    "hook": "pre",
    "outcome": "allowed",
    "policy_name": "pol",
    "decision_type": "deny",
    "reason": "r",
    "group_id": "g",
    "phase": "start",
    "agent_ids": ["planner", "worker"],
    "content": "hello",
    "total_messages": 2,
    "compressed_count": 1,
    "worker_id": "w1",
}


@requires_otel
def test_every_registered_kind_converts(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "mas_call_start",
            "call_id": "run",
            "timestamp": 1000.0,
            "run_id": "r1",
            "session_id": "s1",
            "agent_id": "orch",
            "app_name": "cov",
        }
    ]
    ts = 1000.0
    for i, kind in enumerate(registered_kinds()):
        ts += 0.01
        ev = {"kind": kind, "timestamp": ts, "parent_call_id": "run", **_FIELDS}
        # give *_start/_end pairs a matching call_id so intervals open+close
        if kind.endswith(("_start", "_end")) or kind in ("mas_call_end",):
            ev["call_id"] = f"c{i}" if kind.endswith("_start") else f"c{i - 1}"
        events.append(ev)
    events.append(
        {
            "kind": "mas_call_end",
            "call_id": "run",
            "timestamp": ts + 1,
            "agent_id": "orch",
        }
    )

    ep = tmp_path / "e.jsonl"
    ep.write_text("\n".join(json.dumps(e) for e in events))
    op = tmp_path / "s.jsonl"
    # All layers on so governance/provenance handlers run too.
    n = replay_events_file(
        ep,
        op,
        service_name="svc",
        export_layers={"governance": True, "provenance": True},
        converter_profile="raw",
    )
    assert n == len(events)
    spans = [json.loads(line) for line in op.read_text().splitlines() if line.strip()]
    # A healthy spread of boundaries got emitted (handlers ran without error).
    boundaries = {s["attributes"].get("mas.boundary") for s in spans}
    assert {
        "TaskCall",
        "LLMCall",
        "ToolCall",
        "MemoryCall",
        "GovernanceEvent",
        "CallAnnotation",
    } <= boundaries
