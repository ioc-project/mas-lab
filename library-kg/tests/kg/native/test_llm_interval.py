#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Current LLM intervals stamp the request on start; the end is the return."""

from __future__ import annotations

import pytest

from mas.library.kg.observability.native.validate import EventValidator
from mas.library.kg.pipeline import build_kg_document

_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"
_MESSAGES = [{"role": "user", "content": "renew the deal"}]
_TOOLS = ["lookup_quote"]
_TOOL_CALLS = [{"toolName": "lookup_quote", "arguments": {"id": "1"}}]


def _current_events() -> list[dict]:
    return [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "r1",
            "call_id": "exec",
            "agent_id": "planner",
            "input": "renew the deal",
        },
        {
            "kind": "llm_call_start",
            "timestamp": 1.1,
            "run_id": "r1",
            "call_id": "llm",
            "parent_call_id": "exec",
            "agent_id": "planner",
            "model": _MODEL,
            "messages": _MESSAGES,
            "tools": _TOOLS,
            "max_tokens": 4096,
            "temperature": 0.0,
        },
        {
            "kind": "llm_call_end",
            "timestamp": 1.2,
            "run_id": "r1",
            "call_id": "llm",
            "parent_call_id": "exec",
            "agent_id": "planner",
            "output": "",
            "finish_reason": "tool_calls",
            "tool_calls": _TOOL_CALLS,
            "tokens_used": {
                "prompt_tokens": 80,
                "completion_tokens": 18,
                "total_tokens": 98,
            },
            "latency_ms": 1500,
        },
        {
            "kind": "tool_call_start",
            "timestamp": 1.3,
            "run_id": "r1",
            "call_id": "tool",
            "parent_call_id": "exec",
            "agent_id": "planner",
            "tool_name": "lookup_quote",
            "arguments": {"id": "1"},
        },
        {
            "kind": "tool_call_end",
            "timestamp": 1.4,
            "run_id": "r1",
            "call_id": "tool",
            "parent_call_id": "exec",
            "agent_id": "planner",
            "tool_name": "lookup_quote",
            "status": "success",
            "result": {"ok": True},
        },
        {
            "kind": "execution_end",
            "timestamp": 1.5,
            "run_id": "r1",
            "call_id": "exec",
            "agent_id": "planner",
            "status": "ok",
            "output": "done",
        },
    ]


def _llm_node(doc: dict) -> dict:
    return next(n for n in doc["nodes"] if n.get("node_type") == "LLMCall")


def test_current_llm_interval_has_no_request_fields_on_end() -> None:
    start = next(e for e in _current_events() if e["kind"] == "llm_call_start")
    end = next(e for e in _current_events() if e["kind"] == "llm_call_end")
    assert start["model"] == _MODEL
    assert "model" not in end
    assert "tools" not in end
    assert "messages" not in end


def test_current_llm_interval_passes_recommended_shapes() -> None:
    report = EventValidator().validate(_current_events(), strictness="recommended")
    assert not report.errors(), report.errors()
    missing = {
        (w.kind, w.field) for w in report.warnings("L3") if str(w.kind).startswith("llm_call")
    }
    assert ("llm_call_start", "model") not in missing
    assert ("llm_call_start", "messages") not in missing
    assert ("llm_call_start", "tools") not in missing
    assert ("llm_call_start", "max_tokens") not in missing
    assert ("llm_call_start", "temperature") not in missing
    assert ("llm_call_end", "finish_reason") not in missing
    assert ("llm_call_end", "tokens_used") not in missing
    assert ("llm_call_end", "tool_calls") not in missing


def test_current_llm_interval_preserves_request_and_ontology() -> None:
    doc = build_kg_document(_current_events(), run_id="r1", all_attributes=True)
    llm = _llm_node(doc)
    assert llm["provider"] == "bedrock"
    assert llm["modelName"] == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert "renew the deal" in str(llm.get("prompt") or "")
    assert llm["temperature"] == 0.0
    assert llm["maxTokens"] == 4096
    assert llm["offeredTools"] == _TOOLS
    assert llm["finishReason"] == "tool_calls"
    assert llm["promptTokenCount"] == 80
    assert llm["completionTokenCount"] == 18
    assert llm["totalTokenCount"] == 98
    assert llm["toolCalls"][0]["toolName"] == "lookup_quote"
    assert "lookup_quote" in str(llm.get("completion") or "")
    llm_id = str(llm.get("id") or llm.get("callId") or "")
    assert any(
        e.get("edge_type") == "hasLLMCall" and e.get("to_id") == llm_id for e in doc["edges"]
    )
    assert any(e.get("edge_type") == "executesLLM" for e in doc["edges"])


def test_tool_call_text_matches_otel_rendering() -> None:
    """Same completion text the OTel converter renders (see its test)."""
    events = _current_events()
    for event in events:
        if event["kind"] == "llm_call_end":
            event["output"] = "ok"
            event["tool_calls"] = [{"toolName": "lookup_quote", "arguments": {"id": "1"}}]
    llm = _llm_node(build_kg_document(events, run_id="r1", all_attributes=True))
    assert llm["completion"] == 'ok\n→ lookup_quote({"id": "1"})'


def test_parse_tool_call_accepts_every_shape() -> None:
    from mas.library.kg.observability.native.handlers.chat import _parse_tool_call

    assert _parse_tool_call({"id": "c1", "function": {"name": "a", "arguments": '{"x": 1}'}}) == {
        "id": "c1",
        "name": "a",
        "arguments": '{"x": 1}',
    }
    assert _parse_tool_call({"toolName": "b", "arguments": {"y": 2}}) == {
        "id": "",
        "name": "b",
        "arguments": '{"y": 2}',
    }
    assert _parse_tool_call({"tool_call_id": "t9"}) == {
        "id": "t9",
        "name": "tool",
        "arguments": "{}",
    }


def test_tool_call_text_helpers() -> None:
    from mas.library.kg.observability.native.handlers.chat import (
        _format_tool_calls,
        _text_with_tool_calls,
    )

    calls = [{"toolName": "a", "arguments": {}}, "junk"]
    assert _format_tool_calls(calls) == "→ a({})"
    assert _format_tool_calls(None) == ""
    assert _format_tool_calls([{"name": "t", "arguments": "z" * 500}]) == f"→ t({'z' * 300})"
    assert _text_with_tool_calls("hi", calls) == "hi\n→ a({})"
    assert _text_with_tool_calls("", calls) == "→ a({})"
    assert _text_with_tool_calls("hi", None) == "hi"


def test_start_without_model_keeps_provider_unknown() -> None:
    events = _current_events()
    for event in events:
        if event["kind"] == "llm_call_start":
            event.pop("model")
    llm = _llm_node(build_kg_document(events, run_id="r1", all_attributes=True))
    assert llm["modelName"] == ""
    assert llm["provider"] == "unknown"


def test_parse_tool_call_ignores_non_dict_function() -> None:
    from mas.library.kg.observability.native.handlers.chat import _parse_tool_call

    call = _parse_tool_call({"function": "oops", "name": "lookup", "arguments": {"q": 1}})
    assert call == {"id": "", "name": "lookup", "arguments": '{"q": 1}'}


def _with_session_identity(events: list[dict]) -> list[dict]:
    """Events as the runtime writes them: empty run_id, session_id and app_name set."""
    out = []
    for event in events:
        out.append({**event, "run_id": "", "session_id": "sess-uuid", "app_name": "trip-planner"})
    return out


def test_llm_call_is_named_by_its_model() -> None:
    """A call is named by what it executes: the model, as its LLM catalog entry is."""
    doc = build_kg_document(_current_events(), run_id="r1")
    (llm,) = [n for n in doc["nodes"] if n.get("node_type") == "LLM"]
    assert _llm_node(doc)["name"] == llm["name"] == _MODEL.split("/", 1)[1]


def test_llm_catalog_entry_takes_the_provider_of_its_calls() -> None:
    doc = build_kg_document(_current_events(), run_id="r1")
    (llm,) = [n for n in doc["nodes"] if n.get("node_type") == "LLM"]
    assert llm["provider"] == "bedrock"


def test_session_uses_the_session_id_and_app_name_its_events_carry() -> None:
    """The OTel path takes both from the same events; so must the native path."""
    events = _with_session_identity(_current_events())
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert session["id"] == "sess-uuid"
    assert session["appName"] == "trip-planner"
    assert session["name"] == "Session sess-uuid"
    (mas,) = [n for n in doc["nodes"] if n.get("node_type") == "MAS"]
    assert mas["name"] == "trip-planner"


def test_session_without_event_identity_keeps_the_run_id_form() -> None:
    doc = build_kg_document(_current_events(), run_id="r1")
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert session["id"] == "session-r1"


def test_session_success_follows_the_mas_call() -> None:
    events = _current_events() + [
        {"kind": "mas_call_start", "timestamp": 0.5, "run_id": "r1", "call_id": "mc"},
        {
            "kind": "mas_call_end",
            "timestamp": 2.0,
            "run_id": "r1",
            "call_id": "mc",
            "status": "error",
        },
    ]
    doc = build_kg_document(events, run_id="r1")
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    mas_call = next(n for n in doc["nodes"] if n.get("node_type") == "MASCall")
    assert session["success"] == mas_call["success"] is False


def test_a_session_without_a_mas_call_has_no_outcome() -> None:
    doc = build_kg_document(_current_events(), run_id="r1")
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert "success" not in session


def test_a_call_with_no_end_event_has_no_end_and_no_verdict() -> None:
    """An unfinished call is not given an end or an outcome it never reported."""
    events = [e for e in _current_events() if e["kind"] != "llm_call_end"]
    doc = build_kg_document(events, run_id="r1")
    llm = _llm_node(doc)
    assert "endTime" not in llm
    assert "success" not in llm


def test_a_call_that_ended_keeps_its_own_verdict() -> None:
    doc = build_kg_document(_current_events(), run_id="r1")
    assert _llm_node(doc)["success"] is True


def test_a_hierarchical_run_id_does_not_hide_the_session_id_the_events_carry() -> None:
    """``--run-id app/exp/sc/it/r1`` names the app; the Session id is the one the events carry."""
    events = _with_session_identity(_current_events())
    run_id = "trip-planner/demo/baseline/item1/r1"
    doc = build_kg_document(events, run_id=run_id, all_attributes=True)
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert session["id"] == "sess-uuid"
    assert session["appName"] == "trip-planner"
    assert {n["sessionId"] for n in doc["nodes"] if n.get("sessionId")} == {"sess-uuid"}


def test_a_hierarchical_run_id_is_the_session_id_when_the_events_carry_none() -> None:
    events = [{k: v for k, v in e.items() if k != "session_id"} for e in _current_events()]
    run_id = "trip-planner/demo/baseline/item1/r1"
    doc = build_kg_document(events, run_id=run_id, all_attributes=True)
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert session["id"] == run_id
    assert session["appName"] == "trip-planner"


def test_no_split_session_id_keeps_the_run_id_form() -> None:
    events = _with_session_identity(_current_events())
    doc = build_kg_document(events, run_id="r1", split_session_id=False, app_name="x")
    (session,) = [n for n in doc["nodes"] if n.get("node_type") == "Session"]
    assert session["id"] == "session-r1"


def test_event_tool_calls_win_over_an_openai_shaped_response_on_the_same_event() -> None:
    events = _current_events()
    end = next(e for e in events if e["kind"] == "llm_call_end")
    end["response"] = {"content": "x"}
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    llm = _llm_node(doc)
    assert llm["completion"] == 'x\n→ lookup_quote({"id": "1"})'
    assert [c["toolName"] for c in llm["toolCalls"]] == ["lookup_quote"]


def test_a_malformed_temperature_or_cap_does_not_crash_the_start_handler() -> None:
    events = _current_events()
    start = next(e for e in events if e["kind"] == "llm_call_start")
    start.update(temperature="auto", max_tokens="")
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    llm = _llm_node(doc)
    assert llm["temperature"] == 0.0
    assert "maxTokens" not in llm


def test_offered_tools_are_names_even_when_given_as_openai_tool_dicts() -> None:
    events = _current_events()
    start = next(e for e in events if e["kind"] == "llm_call_start")
    start["tools"] = [{"type": "function", "function": {"name": "lookup_quote"}}, "other"]
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    assert _llm_node(doc)["offeredTools"] == ["lookup_quote", "other"]


def test_the_cap_on_the_return_is_the_one_actually_sent() -> None:
    events = _current_events()
    next(e for e in events if e["kind"] == "llm_call_end")["max_tokens"] = 1024
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    assert _llm_node(doc)["maxTokens"] == 1024


def test_an_llm_call_that_ended_in_error_did_not_succeed() -> None:
    events = _current_events()
    next(e for e in events if e["kind"] == "llm_call_end")["status"] = "error"
    assert _llm_node(build_kg_document(events, run_id="r1"))["success"] is False


def test_openai_shaped_reply_text_survives_event_level_tool_calls() -> None:
    events = _current_events()
    end = next(e for e in events if e["kind"] == "llm_call_end")
    end["response"] = {"choices": [{"message": {"content": "Let me check"}}]}
    llm = _llm_node(build_kg_document(events, run_id="r1", all_attributes=True))
    assert llm["completion"].startswith("Let me check")


def test_a_tool_result_that_is_falsy_but_not_empty_is_kept() -> None:
    events = _current_events()
    end = next(e for e in events if e["kind"] == "tool_call_end")
    end["output"] = []
    doc = build_kg_document(events, run_id="r1", all_attributes=True)
    tool = next(n for n in doc["nodes"] if n.get("node_type") == "ToolCall")
    assert tool["toolOutput"] == "[]"


def test_reasoning_fields_appear_only_when_the_events_carry_them() -> None:
    """No ``reasoningTokenCount=0`` or ``reasoningEffort=""`` is made up for a plain call."""
    llm = _llm_node(build_kg_document(_current_events(), run_id="r1"))
    assert "reasoningTokenCount" not in llm
    assert "reasoningEffort" not in llm


@pytest.mark.scope_next
def test_reasoning_effort_and_tokens_are_read_from_the_records() -> None:
    events = _current_events()
    start = next(e for e in events if e["kind"] == "llm_call_start")
    end = next(e for e in events if e["kind"] == "llm_call_end")
    start["reasoning_effort"] = "high"
    end["tokens_used"] = {
        "prompt_tokens": 3,
        "completion_tokens": 9,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    llm = _llm_node(build_kg_document(events, run_id="r1"))
    assert llm["reasoningEffort"] == "high"
    assert llm["reasoningTokenCount"] == 0  # stated by the provider, so kept
