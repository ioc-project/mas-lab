# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Live assembly order must preserve the actual model input in exported spans."""

import json

import pytest

from tests.conftest import requires_otel


def event(kind, agent="planner", call="shared", **fields):
    return {"kind": kind, "agent_id": agent, "call_id": call, "timestamp": 1.0, **fields}


def exporter(tmp_path, profile="observe_sdk"):
    from mas.library.telemetry.conversion.session import create_otel_export

    return create_otel_export(
        spans_path=tmp_path / "spans.jsonl", app_name="example", converter_profile=profile, realtime=True
    )


def llm_spans(tmp_path):
    return [
        row
        for line in (tmp_path / "spans.jsonl").read_text().splitlines()
        if (row := json.loads(line))["attributes"].get("mas.boundary") == "LLMCall"
    ]


@requires_otel
@pytest.mark.parametrize("profile", ["raw", "observe_sdk"])
@pytest.mark.parametrize("early", [True, False])
@pytest.mark.parametrize("explicit_reference", [True, False])
def test_assembled_input_survives_both_event_orders(tmp_path, profile, early, explicit_reference):
    export = exporter(tmp_path, profile)
    messages = [{"role": "user", "content": "Find the outstanding orders"}]
    context = event("context_assembled", messages=messages, total_tokens=7)
    if explicit_reference:
        context["llm_call_id"] = "shared"
    start = event("llm_call_start")
    for row in (context, start) if early else (start, context):
        export.process_event(row)
    export.process_event(event("llm_call_end", output="Three orders"))
    export.close()
    spans = llm_spans(tmp_path)
    assert len(spans) == 1
    attributes = spans[0]["attributes"]
    assert json.loads(attributes["mas.llm.messages"]) == messages
    if profile == "observe_sdk":
        assert json.loads(attributes["gen_ai.input.messages"]) == messages
        assert json.loads(attributes["ioa_observe.entity.input"]) == messages
    assert attributes["mas.llm.response"] == "Three orders"


@requires_otel
def test_same_call_id_from_different_agents_keeps_separate_inputs(tmp_path):
    export = exporter(tmp_path)
    for agent in ("planner", "worker"):
        export.process_event(event("context_assembled", agent, messages=[{"role": "user", "content": agent}]))
    for agent in ("worker", "planner"):
        export.process_event(event("llm_call_start", agent))
        export.process_event(event("llm_call_end", agent))
    export.close()
    spans = llm_spans(tmp_path)
    assert len(spans) == 2
    for span in spans:
        attrs = span["attributes"]
        assert json.loads(attrs["gen_ai.input.messages"])[0]["content"] == attrs["agent_id"]


@requires_otel
@pytest.mark.parametrize("cleanup", ["reset_run", "finish"])
def test_unmatched_context_is_discarded_on_cleanup(tmp_path, cleanup):
    export = exporter(tmp_path)
    export.process_event(event("context_assembled", messages=[{"role": "user", "content": "old context"}]))
    getattr(export.converter, cleanup)()
    export.process_event(event("llm_call_start"))
    export.process_event(event("llm_call_end"))
    export.close()
    assert "old context" not in json.dumps(llm_spans(tmp_path))


@requires_otel
def test_late_context_does_not_reopen_a_completed_call(tmp_path):
    export = exporter(tmp_path)
    export.process_event(event("llm_call_start", messages=[{"role": "user", "content": "original"}]))
    export.process_event(event("llm_call_end"))
    export.process_event(event("context_assembled", messages=[{"role": "user", "content": "late"}]))
    assert not export.converter._pending_llm_contexts
    export.close()
    assert len(llm_spans(tmp_path)) == 1
    assert "late" not in json.dumps(llm_spans(tmp_path))


@requires_otel
@pytest.mark.parametrize("profile", ["raw", "observe_sdk"])
@pytest.mark.parametrize("filtered", [False, True])
def test_native_run_end_discards_only_its_pending_prompts(tmp_path, monkeypatch, profile, filtered):
    export = exporter(tmp_path, profile)
    if filtered:
        import mas.library.telemetry.conversion.converter as module

        native = module.should_export_event
        monkeypatch.setattr(
            module, "should_export_event", lambda ev, layers: ev["kind"] != "mas_call_end" and native(ev, layers)
        )
    for run in ("one", "two"):
        export.process_event(event("context_assembled", run_id=run, messages=[{"role": "user", "content": run}]))
    export.process_event(event("mas_call_end", agent="mas", call="run-one", run_id="one"))
    export.process_event(event("llm_call_start", run_id="two"))
    export.process_event(event("llm_call_end", run_id="two"))
    export.process_event(event("llm_call_start", run_id="three"))
    export.process_event(event("llm_call_end", run_id="three"))
    export.close()
    spans = llm_spans(tmp_path)
    assert len(spans) == 2
    assert json.loads(spans[0]["attributes"]["mas.llm.messages"])[0]["content"] == "two"
    assert "one" not in spans[1]["attributes"].get("mas.llm.messages", "")
    assert not export.converter._pending_llm_contexts
