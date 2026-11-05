#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Converter / replay behaviour — requires the OTel SDK."""

from __future__ import annotations

import json

from tests.conftest import requires_otel


@requires_otel
def test_span_tree_and_names(spans):
    # Default layers = structure + execution + semantic + trajectory (governance OFF).
    names = sorted(s["name"] for s in spans)
    assert "TaskCall" in names
    assert "AgentCall" in names
    assert "LLMCall" in names
    assert "ToolCall" in names
    assert "MemoryCall" in names
    assert "GovernanceEvent" not in names  # governance layer off by default


@requires_otel
def test_exactly_one_root(spans):
    roots = [s for s in spans if not s.get("parent_id")]
    assert len(roots) == 1
    assert roots[0]["name"] == "root"
    task = next(s for s in spans if s["name"] == "TaskCall")
    assert task["parent_id"] == roots[0]["context"]["span_id"]


@requires_otel
def test_call_ids_and_parent_linking(spans):
    by_call = {s["attributes"].get("mas.call.id"): s for s in spans}
    assert {"run", "a1", "l1", "t1"} <= set(by_call)
    # a1's parent span is the TaskCall (run)
    a1 = by_call["a1"]
    run = by_call["run"]
    assert a1["parent_id"] == run["context"]["span_id"]


@requires_otel
def test_llm_tokens_and_response(spans):
    llm = next(s for s in spans if s["name"] == "LLMCall")
    a = llm["attributes"]
    assert a["metrics.token.total"] == 15
    assert a["metrics.token.input"] == 10
    assert "here is the plan" in a["mas.llm.response"]


@requires_otel
def test_app_name_overlay(spans):
    for s in spans:
        assert s["attributes"].get("application_id") == "test-app"
        assert s["attributes"].get("session.name") == "test-app"


@requires_otel
def test_governance_layer_off_by_default(tmp_path, events_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    out = tmp_path / "no_gov.jsonl"
    # governance defaults OFF → the governance_checked point span is suppressed
    replay_events_file(events_path, out, service_name="s", app_name="a", converter_profile="raw")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert not any(s["name"] == "GovernanceEvent" for s in spans)


@requires_otel
def test_deterministic_replay(tmp_path, events_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    replay_events_file(events_path, a, service_name="mas-runtime", app_name="x")
    replay_events_file(events_path, b, service_name="mas-runtime", app_name="x")
    assert a.read_text() == b.read_text()


@requires_otel
def test_sdk_unavailable_error_is_typed():
    # Construction failure path is covered indirectly; assert the class exists.
    from mas.library.telemetry.exceptions import (
        ConversionError,
        OtelSdkUnavailableError,
    )

    assert issubclass(OtelSdkUnavailableError, ConversionError)
