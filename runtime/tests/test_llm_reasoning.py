#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reasoning / thinking settings from spec.models[] and message sanitization."""

from __future__ import annotations

import pytest
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    ReasoningStream,
    ThinkTagSplitter,
    apply_reasoning_payload,
    reasoning_settings_from_manifest,
    reasoning_text,
    sanitize_assistant_message,
    think_block_text,
)


def test_reasoning_defaults_exclude_true_without_sending_extension():
    settings = reasoning_settings_from_manifest({"spec": {"models": [{"model": "gpt-4o"}]}})
    assert settings.effort is None
    assert settings.budget_tokens is None
    assert settings.exclude is True
    assert settings.exclude_from_spec is False
    payload = apply_reasoning_payload({"max_tokens": 100, "model": "m"}, settings, max_tokens=100)
    assert payload["max_tokens"] == 100
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload


def test_nested_reasoning_block_wins_over_alias():
    settings = reasoning_settings_from_manifest(
        {
            "spec": {
                "models": [
                    {
                        "model": "gpt-5",
                        "reasoning_effort": "high",
                        "reasoning": {"effort": "low", "budget_tokens": 128, "exclude": True},
                    }
                ]
            }
        }
    )
    assert settings.effort == "low"
    assert settings.budget_tokens == 128
    assert settings.exclude is True
    assert settings.exclude_from_spec is True


def test_deprecated_spec_llm_reasoning_effort():
    settings = reasoning_settings_from_manifest({"spec": {"llm": {"reasoning_effort": "minimal"}}})
    assert settings.effort == "minimal"


def test_deprecated_spec_llm_nested_reasoning():
    settings = reasoning_settings_from_manifest(
        {"spec": {"llm": {"reasoning": {"effort": "low", "budget_tokens": 8, "exclude": True}}}}
    )
    assert settings.effort == "low"
    assert settings.budget_tokens == 8
    assert settings.exclude_from_spec is True


def test_exclude_false_is_honored():
    settings = reasoning_settings_from_manifest({"spec": {"models": [{"model": "m", "reasoning": {"exclude": False}}]}})
    assert settings.exclude is False
    assert settings.exclude_from_spec is True


def test_unknown_effort_is_rejected():
    with pytest.raises(ValueError, match="unsupported reasoning.effort"):
        reasoning_settings_from_manifest({"spec": {"models": [{"model": "m", "reasoning": {"effort": "ludicrous"}}]}})


def test_apply_payload_effort_switches_to_max_completion_tokens():
    settings = ReasoningSettings(effort="low")
    payload = apply_reasoning_payload({"model": "gpt-5", "max_tokens": 2000}, settings, max_tokens=2000)
    assert payload["reasoning_effort"] == "low"
    assert "reasoning" not in payload
    assert payload["max_completion_tokens"] == 2000
    assert "max_tokens" not in payload


def test_apply_payload_none_effort_keeps_max_tokens():
    settings = ReasoningSettings(effort="none")
    payload = apply_reasoning_payload({"max_tokens": 50}, settings, max_tokens=50)
    assert payload["reasoning_effort"] == "none"
    assert payload["max_tokens"] == 50
    assert "max_completion_tokens" not in payload


def test_apply_payload_budget_and_explicit_exclude():
    settings = ReasoningSettings(budget_tokens=256, exclude=True, exclude_from_spec=True)
    payload = apply_reasoning_payload({"max_tokens": 80}, settings, max_tokens=80)
    assert payload["reasoning"] == {"max_tokens": 256, "exclude": True}
    assert payload["max_tokens"] == 80


def test_sanitize_strips_cot_fields_and_think_tags():
    raw = {
        "role": "assistant",
        "content": "<think>secret plan</think>\nVisible answer",
        "reasoning": "hidden",
        "reasoning_content": "also hidden",
        "thinking": "trace",
        "tool_calls": [],
    }
    out = sanitize_assistant_message(raw, exclude=True)
    assert out["content"] == "Visible answer"
    assert "reasoning" not in out
    assert "reasoning_content" not in out
    assert "thinking" not in out
    kept = sanitize_assistant_message(raw, exclude=False)
    assert kept["reasoning"] == "hidden"
    assert "<think>" in str(kept["content"])


def _answer(pieces):
    return "".join(text for kind, text in pieces if kind == "answer")


def test_splitter_holds_split_think_tags_and_returns_reasoning():
    split = ThinkTagSplitter()
    assert split.feed("<thi") == []
    assert split.feed("nk>abc</th") == [("reasoning", "abc")]
    assert split.feed("ink>OK") == [("reasoning_end", ""), ("answer", "OK")]
    assert split.feed(" more") == [("answer", " more")]


def test_splitter_reads_thinking_tags():
    pieces = ThinkTagSplitter().feed("<thinking>hidden</thinking>visible")
    assert pieces == [("reasoning", "hidden"), ("reasoning_end", ""), ("answer", "visible")]
    assert _answer(pieces) == "visible"


def test_reasoning_text_reads_every_reasoning_key():
    assert reasoning_text({"reasoning_content": "a", "content": "x"}) == "a"
    assert reasoning_text({"reasoning_details": [{"type": "reasoning.text", "text": "b"}, {"summary": "c"}]}) == "bc"
    assert think_block_text("<think>p</think>answer<thinking>q</thinking>") == "pq"


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_reasoning_stream_reports_real_boundaries_as_deltas_arrive():
    clock, events = _Clock(), []
    stream = ReasoningStream(lambda b, t, at: events.append((b, t, at)), clock=clock)
    split = ThinkTagSplitter()
    assert stream.feed_delta({"role": "assistant"}, split) == ""
    assert events == []
    clock.now = 101.0
    stream.feed_delta({"reasoning_content": "step 1. "}, split)
    assert events == [("start", "", 101.0)]  # emitted at the first reasoning delta, not at the response
    clock.now = 102.5
    stream.feed_delta({"reasoning_content": "step 2."}, split)
    clock.now = 103.0
    assert stream.feed_delta({"content": "Answer"}, split) == "Answer"
    clock.now = 104.0
    stream.feed_delta({"reasoning_content": "late"}, split)
    stream.close()
    assert events == [("start", "", 101.0), ("end", "step 1. step 2.", 103.0)]


def test_reasoning_stream_think_tag_close_ends_the_interval():
    clock, events = _Clock(), []
    stream = ReasoningStream(lambda b, t, at: events.append((b, t, at)), clock=clock)
    split = ThinkTagSplitter()
    stream.feed_delta({"content": "<think>pl"}, split)
    clock.now = 101.0
    stream.feed_delta({"content": "an</think>"}, split)
    clock.now = 102.0
    assert stream.feed_delta({"content": "done"}, split) == "done"
    assert events == [("start", "", 100.0), ("end", "plan", 101.0)]


def test_reasoning_stream_tool_call_delta_ends_the_interval_and_stream_end_closes():
    events = []
    stream = ReasoningStream(lambda b, t, at: events.append(b))
    stream.feed_delta({"reasoning": "r"})
    stream.feed_delta({"tool_calls": [{"index": 0}]})
    assert events == ["start", "end"]
    open_only = []
    stream = ReasoningStream(lambda b, t, at: open_only.append(b))
    stream.feed_delta({"reasoning": "r"})
    stream.close()
    assert open_only == ["start", "end"]


def test_reasoning_received_whole_is_a_zero_length_interval_at_receipt():
    clock, events = _Clock(), []
    stream = ReasoningStream(lambda b, t, at: events.append((b, t, at)), clock=clock)
    stream.received("")
    assert events == []  # no reasoning text, no interval (a token count is not one)
    stream.received("all of it")
    stream.received("again")
    assert events == [("start", "", 100.0), ("end", "all of it", 100.0)]


def test_to_spec_dict_omits_default_exclude():
    assert ReasoningSettings(effort="low").to_spec_dict() == {"effort": "low"}
    assert ReasoningSettings(exclude=True, exclude_from_spec=True).to_spec_dict() == {"exclude": True}
    assert ReasoningSettings(exclude=False).to_spec_dict() == {"exclude": False}


def test_mode_think_include_round_trip():
    settings = reasoning_settings_from_manifest(
        {
            "spec": {
                "models": [
                    {
                        "model": "gpt-5",
                        "reasoning": {
                            "effort": "low",
                            "mode": "pro",
                            "think": True,
                            "include": ["reasoning.encrypted_content"],
                            "exclude": True,
                        },
                    }
                ]
            }
        }
    )
    assert settings.mode == "pro"
    assert settings.think is True
    assert settings.include == ("reasoning.encrypted_content",)
    payload = apply_reasoning_payload({"max_tokens": 40}, settings, max_tokens=40)
    assert payload["think"] is True
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["reasoning"]["mode"] == "pro"
    assert payload["reasoning"]["exclude"] is True
    assert payload["reasoning_effort"] == "low"
    assert "effort" not in payload["reasoning"]


def test_catalog_omits_unsupported_reasoning_fields():
    settings = ReasoningSettings(effort="low", mode="pro", think=True, exclude=True, exclude_from_spec=True)
    payload = apply_reasoning_payload(
        {"model": "azure/gpt-4o-mini", "max_tokens": 80},
        settings,
        max_tokens=80,
    )
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload
    assert "think" not in payload
    assert payload["max_tokens"] == 80


def test_catalog_clamps_thinking_budget():
    settings = ReasoningSettings(budget_tokens=99_999, exclude=True)
    payload = apply_reasoning_payload(
        {"model": "gemini-2.5-pro", "max_tokens": 80},
        settings,
        max_tokens=80,
    )
    assert payload["reasoning"]["max_tokens"] == 32768


def test_vllm_think_maps_to_chat_template_kwargs():
    settings = ReasoningSettings(think=True, exclude=True)
    payload = apply_reasoning_payload(
        {"model": "onprem/gemma4", "max_tokens": 40},
        settings,
        max_tokens=40,
    )
    assert "think" not in payload
    assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert payload["max_tokens"] == 40
    assert "max_completion_tokens" not in payload


def test_vllm_think_level_preserved_in_chat_template_kwargs():
    settings = ReasoningSettings(think="high", exclude=True)
    payload = apply_reasoning_payload(
        {"model": "onprem/gemma4", "max_tokens": 40},
        settings,
        max_tokens=40,
    )
    kwargs = payload["extra_body"]["chat_template_kwargs"]
    assert kwargs["enable_thinking"] is True
    assert kwargs["thinking_level"] == "high"
    assert payload["max_tokens"] == 40


def test_catalog_defaults_fill_omitted_effort():
    settings = reasoning_settings_from_manifest({"spec": {"models": [{"model": "gpt-5"}]}})
    assert settings.effort == "medium"


def test_settings_from_matching_model_row():
    settings = reasoning_settings_from_manifest(
        {
            "spec": {
                "models": [
                    {"id": "main", "model": "gpt-4o", "reasoning": {"effort": "none"}},
                    {"id": "thinker", "model": "gpt-5-mini", "reasoning": {"effort": "high"}},
                ]
            }
        },
        model="gpt-5-mini",
    )
    assert settings.effort == "high"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported reasoning.mode"):
        reasoning_settings_from_manifest({"spec": {"models": [{"model": "m", "reasoning": {"mode": "turbo"}}]}})
