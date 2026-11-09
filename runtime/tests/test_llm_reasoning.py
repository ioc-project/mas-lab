#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reasoning / thinking settings from spec.models[] and message sanitization."""

from __future__ import annotations

import pytest
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    ThinkTagStreamFilter,
    apply_reasoning_payload,
    reasoning_settings_from_manifest,
    sanitize_assistant_message,
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


def test_stream_filter_drops_split_think_tags():
    filt = ThinkTagStreamFilter()
    assert filt.feed("<thi") == ""
    assert filt.feed("nk>abc</th") == ""
    assert filt.feed("ink>OK") == "OK"
    assert filt.feed(" more") == " more"


def test_stream_filter_drops_thinking_tags():
    filt = ThinkTagStreamFilter()
    assert filt.feed("<thinking>hidden</thinking>visible") == "visible"


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
