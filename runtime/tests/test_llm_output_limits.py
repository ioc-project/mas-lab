#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Output-token limits: resolution, ceiling, single-field payload, truncation policy."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.llm_output_limits import (
    OutputLimits,
    TruncationPolicy,
    estimate_model_cost_usd,
    output_token_kwargs,
    resolve_output_limits,
)
from mas.runtime.engine.llm_request import apply_output_token_limit
from mas.runtime.schema.egress import InvokeEngineIo


def _manifest(**row: Any) -> dict[str, Any]:
    return {"spec": {"models": [{"id": "main", "model": "onprem/qwen3", **row}]}}


def test_no_configuration_sends_no_limit() -> None:
    limits = resolve_output_limits(_manifest(), model="onprem/qwen3", env={})
    assert limits.max_tokens is None
    assert limits.max_completion_tokens is None
    assert limits.budget is None
    assert limits.truncation.action == "warn"


def test_precedence_override_manifest_env_infra() -> None:
    generation = {"max_tokens": 1000}
    assert resolve_output_limits(_manifest(), model="m", generation=generation, env={}).max_tokens == 1000
    env = {"MAS_LLM_MAX_TOKENS": "3000"}
    assert resolve_output_limits(_manifest(), model="m", generation=generation, env=env).max_tokens == 3000
    assert (
        resolve_output_limits(_manifest(max_tokens=2000), model="m", generation=generation, env=env).max_tokens == 2000
    )
    assert (
        resolve_output_limits(_manifest(max_tokens=2000), model="m", env=env, max_tokens_override=4000).max_tokens
        == 4000
    )


def test_infra_ceiling_bounds_every_layer() -> None:
    limits = resolve_output_limits(
        _manifest(max_completion_tokens=20000),
        model="onprem/qwen3",
        generation={"max_output_tokens": 8000},
        max_tokens_override=12000,
        env={},
    )
    assert limits.max_tokens == 8000
    assert limits.max_completion_tokens == 8000
    assert limits.ceiling == 8000


def test_catalog_max_output_tokens_is_a_ceiling() -> None:
    limits = resolve_output_limits(
        {"spec": {"models": [{"model": "gpt-4o", "max_tokens": 50000}]}}, model="gpt-4o", env={}
    )
    assert limits.max_tokens == 16384


def test_context_window_resolves_from_effective_model_catalog() -> None:
    limits = resolve_output_limits(_manifest(), model="vertex_ai/gemini-2.5-flash", env={})
    assert limits.context_window == 1048576


def test_infra_context_window_override_precedes_catalog_but_not_agent_override() -> None:
    generation = {"context_window": 200000}
    assert (
        resolve_output_limits(
            _manifest(), model="vertex_ai/gemini-2.5-flash", generation=generation, env={}
        ).context_window
        == 200000
    )
    assert (
        resolve_output_limits(
            _manifest(context_window=256000),
            model="vertex_ai/gemini-2.5-flash",
            generation=generation,
            env={},
        ).context_window
        == 256000
    )


def test_unknown_model_uses_context_fallback_with_warning(caplog) -> None:
    limits = resolve_output_limits(_manifest(), model="custom-unlisted-context-model", env={})
    assert limits.context_window == 128000
    assert "using fallback 128000" in caplog.text


def test_estimate_model_cost_uses_provider_cached_token_rate() -> None:
    cost = estimate_model_cost_usd(
        "vertex_ai/gemini-2.5-flash",
        {
            "prompt_tokens": 1000000,
            "completion_tokens": 100000,
            "prompt_tokens_details": {"cached_tokens": 400000},
        },
    )
    assert cost == pytest.approx(0.6 * 0.30 + 0.4 * 0.03 + 0.1 * 2.50)


def test_estimate_model_cost_is_unknown_without_catalog_rates() -> None:
    assert estimate_model_cost_usd("custom-proxy-model", {"prompt_tokens": 5, "completion_tokens": 2}) is None


def test_invalid_env_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="MAS_LLM_MAX_TOKENS"):
        resolve_output_limits(_manifest(), model="m", env={"MAS_LLM_MAX_TOKENS": "0"})


def test_truncation_policy_layers_and_shorthand() -> None:
    limits = resolve_output_limits(
        _manifest(on_truncation={"factor": 3, "retries": 2}),
        model="m",
        generation={"on_truncation": "escalate"},
        env={},
    )
    assert limits.truncation == TruncationPolicy(action="escalate", factor=3.0, retries=2)
    env = {"MAS_LLM_ON_TRUNCATION": "error"}
    escalate = _manifest(on_truncation="escalate")
    assert resolve_output_limits(escalate, model="m", env=env).truncation.action == "escalate"
    assert (
        resolve_output_limits(_manifest(), model="m", generation={"on_truncation": "ignore"}, env=env).truncation.action
        == "error"
    )
    with pytest.raises(ValueError, match="on_truncation action"):
        TruncationPolicy.from_spec("retry")
    with pytest.raises(ValueError, match="factor"):
        TruncationPolicy.from_spec({"factor": 1})


def test_fit_to_prompt_and_escalation_bounds() -> None:
    limits = OutputLimits(max_tokens=4000, context_window=10000)
    assert limits.fit_to_prompt(7000).max_tokens == 3000
    assert limits.fit_to_prompt(1000).max_tokens == 4000
    assert limits.fit_to_prompt(12000).max_tokens == 4000
    assert OutputLimits(context_window=10000).fit_to_prompt(9000).budget is None

    policy = TruncationPolicy(action="escalate", factor=2, max_tokens=6000)
    assert OutputLimits(max_tokens=4000, truncation=policy).escalated(4000) == 6000
    assert OutputLimits(max_tokens=6000, truncation=policy).escalated(6000) is None
    assert OutputLimits(truncation=TruncationPolicy(action="escalate")).escalated(8000) == 16000
    assert OutputLimits(max_completion_tokens=100, ceiling=150).escalated(100) == 150


def test_payload_sends_at_most_one_field() -> None:
    base = {"model": "m"}
    assert apply_output_token_limit(base, max_tokens=None, max_completion_tokens=None) == base
    assert apply_output_token_limit(base, max_tokens=10, max_completion_tokens=None)["max_tokens"] == 10
    both = apply_output_token_limit(base, max_tokens=10, max_completion_tokens=20)
    assert both == {"model": "m", "max_completion_tokens": 20}
    mapped = apply_output_token_limit(
        {"model": "m", "max_completion_tokens": 30}, max_tokens=10, max_completion_tokens=None
    )
    assert mapped == {"model": "m", "max_completion_tokens": 30}
    from_extra = apply_output_token_limit({"model": "m", "max_tokens": 5}, max_tokens=10, max_completion_tokens=None)
    assert from_extra["max_tokens"] == 5


def test_output_token_kwargs_for_direct_sdk_callers(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MAS_LLM_MAX_COMPLETION_TOKENS", raising=False)
    assert output_token_kwargs("onprem/qwen3") == {}
    assert output_token_kwargs("onprem/qwen3", generation={"max_tokens": 900}) == {"max_tokens": 900}
    monkeypatch.setenv("MAS_LLM_MAX_COMPLETION_TOKENS", "700")
    assert output_token_kwargs("onprem/qwen3", generation={"max_tokens": 900}) == {"max_completion_tokens": 700}


class _ScriptedProvider:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.replies.pop(0))


def _engine(provider: _ScriptedProvider, **row: Any) -> LiveLlmEngine:
    return LiveLlmEngine(manifest=_manifest(**row), model="onprem/qwen3", llm_provider=provider)


def _length(tokens: int) -> dict[str, Any]:
    return {
        "content": "partial",
        "finish_reason": "length",
        "usage": {"completion_tokens": tokens, "total_tokens": tokens + 10},
    }


def test_engine_omits_limit_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider([{"content": "ok", "finish_reason": "stop"}])
    ret = _engine(provider).invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "ok"
    assert "max_tokens" not in provider.calls[0]
    assert "max_completion_tokens" not in provider.calls[0]
    assert ret.max_tokens is None


def test_engine_escalates_on_length(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider(
        [
            _length(1000),
            {"content": "full", "finish_reason": "stop", "usage": {"completion_tokens": 1500, "total_tokens": 1510}},
        ]
    )
    engine = _engine(provider, max_tokens=1000, on_truncation={"action": "escalate", "max_tokens": 3000})
    ret = engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.text == "full"
    assert [c["max_tokens"] for c in provider.calls] == [1000, 2000]
    assert ret.max_tokens == 2000
    assert ret.truncation_retries == 1
    assert ret.usage["completion_tokens"] == 2500


def test_infra_pricing_override_precedes_catalog_rates() -> None:
    limits = resolve_output_limits(
        _manifest(),
        model="vertex_ai/gemini-2.5-flash",
        generation={
            "pricing": {
                "input_per_million_tokens": 1.0,
                "cached_input_per_million_tokens": 0.5,
                "output_per_million_tokens": 10.0,
            }
        },
        env={},
    )
    assert estimate_model_cost_usd(
        "vertex_ai/gemini-2.5-flash",
        {"prompt_tokens": 100, "completion_tokens": 10},
        pricing_override=limits.pricing,
    ) == pytest.approx((100 * 1.0 + 10 * 10.0) / 1_000_000)


@pytest.mark.asyncio
async def test_async_engine_escalates_on_length(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)

    class AsyncProvider(_ScriptedProvider):
        async def achat_completion(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return dict(self.replies.pop(0))

    provider = AsyncProvider([_length(1000), {"content": "full", "finish_reason": "stop"}])
    engine = _engine(provider, max_tokens=1000, on_truncation={"action": "escalate", "max_tokens": 3000})

    ret = await engine.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))

    assert ret.text == "full"
    assert [call["max_tokens"] for call in provider.calls] == [1000, 2000]
    assert ret.max_tokens == 2000
    assert ret.truncation_retries == 1


def test_engine_escalation_stops_at_retries_and_warns(monkeypatch, caplog) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider([_length(1000), _length(2000)])
    engine = _engine(provider, max_tokens=1000, on_truncation="escalate")
    with caplog.at_level(logging.WARNING):
        ret = engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert len(provider.calls) == 2
    assert ret.finish_reason == "length"
    assert ret.text == "partial"
    assert "truncated" in caplog.text


def test_engine_escalates_from_server_default(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider([_length(8000), {"content": "full", "finish_reason": "stop"}])
    engine = _engine(provider, on_truncation={"action": "escalate", "max_tokens": 12000})
    engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert "max_tokens" not in provider.calls[0]
    assert provider.calls[1]["max_tokens"] == 12000


def test_engine_error_action_returns_engine_error(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider([_length(500)])
    ret = _engine(provider, max_tokens=500, on_truncation="error").invoke(
        InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    )
    assert ret.response_kind == "ERROR"
    assert ret.finish_reason == "length"
    assert "max tokens=500" in ret.text


def test_engine_fits_budget_to_context_window(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    provider = _ScriptedProvider([{"content": "ok", "finish_reason": "stop"}])
    _engine(provider, max_tokens=4000, context_window=1000).invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert provider.calls[0]["max_tokens"] < 1000
