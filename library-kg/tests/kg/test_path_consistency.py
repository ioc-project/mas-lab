#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run scripts/check_path_consistency.py on the small paired fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_EVENTS = _ROOT / "tests" / "fixtures" / "path_consistency" / "events.jsonl"
_SPANS = _ROOT / "tests" / "fixtures" / "path_consistency" / "otel_sdk_spans.jsonl"


def test_path_consistency_script_on_fixture() -> None:
    import importlib.util

    script = _ROOT / "scripts" / "check_path_consistency.py"
    spec = importlib.util.spec_from_file_location("check_path_consistency", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.compare(_EVENTS, _SPANS)
    assert "native_node_types" in report
    assert "otel_node_types" in report
    assert "expected_native_extra_types" in report
    assert "unexpected_core_mismatch" in report
    assert "AgentCall" in report["native_node_types"]
    assert report["unexpected_core_mismatch"] is False
    assert report["core_only_native"] == [] and report["core_only_otel"] == []


_STATE_FIXTURES = (
    "library-kg/tests/fixtures/events_full_ontology.jsonl",
    "library-kg/tests/fixtures/path_consistency/events.jsonl",
    "library-telemetry/tests/fixtures/events.jsonl",
    "docs/tutorials/02-creating-a-mas/fixtures/events.jsonl",
    "docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl",
    "tests/fixtures/multilevel/trip-planner-plain-demo/events.r1.jsonl",
)


@pytest.mark.parametrize("name", _STATE_FIXTURES)
def test_hand_written_traces_have_one_consistent_state_chain(name: str) -> None:
    """Each agent visit is a chain of States: a call starts at the State the previous call
    ended on, and one State id never carries two different contents."""
    repo = Path(__file__).resolve().parents[3]
    lines = (repo / name).read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    content: dict[str, str] = {}
    last_out: dict[str, str] = {}
    parent_of = {e["call_id"]: e.get("parent_call_id") for e in events if e.get("call_id")}
    visits = {e["call_id"] for e in events if e["kind"] == "execution_start"}

    def visit_of(call_id: str | None) -> str | None:
        """The agent visit a call belongs to: itself, or its nearest enclosing visit."""
        while call_id and call_id not in visits:
            call_id = parent_of.get(call_id)
        return call_id

    links = 0
    ends_checked = 0
    start_of: dict[str, dict] = {}
    for event in events:
        visit = visit_of(
            event["call_id"] if event["kind"] == "execution_start" else event.get("parent_call_id")
        )
        for side in ("state_in", "state_out"):
            sid, text = event.get(side), event.get(f"{side}_content")
            if sid and text is not None:
                assert content.setdefault(sid, text) == text, f"{name}: {sid} has two contents"
        if event["kind"].endswith("_start") and event.get("state_in") and visit in last_out:
            assert event["state_in"] == last_out[visit], (
                f"{name}: {event['call_id']} breaks the chain"
            )
            links += 1
        if event["kind"].endswith("_start") and event.get("state_in"):
            start_of[event["call_id"]] = event
        start = start_of.get(event.get("call_id")) if event["kind"].endswith("_end") else None
        if start is not None and event.get("state_in"):
            assert event["state_in"] == start["state_in"], (
                f"{name}: {event['call_id']} ends elsewhere"
            )
            assert event["state_out"] == start["state_out"], (
                f"{name}: {event['call_id']} ends elsewhere"
            )
            ends_checked += 1
        if event["kind"] == "execution_start":
            assert event.get("state_out"), name
        if event["kind"].endswith("_start") and event.get("state_out") and visit:
            last_out[visit] = event["state_out"]
        if event["kind"] == "execution_start":
            last_out[visit] = event["state_out"]
    assert links, f"{name}: no call carries a State chain, so nothing was checked"
    assert ends_checked, f"{name}: no call end repeats its start's States"
