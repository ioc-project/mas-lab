#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.library.standard.plugins.context.conversation import SummarizingConversation
from mas.library.standard.plugins.context.summarizer import DropSummarizer, LlmSummarizer
from mas.runtime.aio import settle
from mas.runtime.contracts.cm_factory import CMFactory
from mas.runtime.registry import get_registry


class _Engine:
    def summarize_messages(self, messages):
        return f"sum:{len(messages)}"


def test_summarizer_plugins_are_registered() -> None:
    reg = get_registry()
    assert reg.resolve_by_type("summarizer", "llm") is not None
    assert reg.resolve_by_type("summarizer", "drop") is not None
    assert isinstance(reg.create("summarizer", {"type": "llm"}), LlmSummarizer)
    assert isinstance(reg.create("summarizer", {"type": "drop"}), DropSummarizer)


async def test_drop_summarizer_keeps_recent_turns() -> None:
    cm = CMFactory.create(
        spec={"type": "summarising", "params": {"keep_turns": 1, "summarizer": "drop"}},
    )
    past = [
        {"role": "user", "content": "old " + "x" * 80},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "b"},
    ]
    out = await settle(cm.manage_history(past, budget_tokens=1))
    assert [m["content"] for m in out] == ["new", "b"]


async def test_llm_summarizer_uses_engine() -> None:
    cm = CMFactory.create(
        spec={"type": "summarising", "params": {"keep_turns": 1, "summarizer": "llm"}},
        engine=_Engine(),
    )
    past = [
        {"role": "user", "content": "old " + "x" * 80},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "b"},
    ]
    out = await settle(cm.manage_history(past, budget_tokens=1))
    assert out[0]["role"] == "system"
    assert "sum:" in out[0]["content"]
    assert out[-1]["content"] == "b"


async def test_llm_summarizer_without_engine_drops(caplog: pytest.LogCaptureFixture) -> None:
    cm = CMFactory.create(
        spec={"type": "summarising", "params": {"keep_turns": 1, "hysteresis_ratio": 0}},
    )
    assert isinstance(cm, SummarizingConversation)
    past = [
        {"role": "user", "content": "old " + "x" * 80},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
    ]
    with caplog.at_level("WARNING"):
        out = await settle(cm.manage_history(past, budget_tokens=1))
    assert [m["role"] for m in out] == ["user"]
    assert out[0]["content"] == "new"
    assert any("no engine" in rec.message for rec in caplog.records)


def test_summarizer_string_shorthand_and_object_form() -> None:
    drop = CMFactory.create(
        spec={"type": "summarising", "params": {"summarizer": {"type": "drop"}}},
    )
    assert drop._summarizer.__class__.__name__ == "DropSummarizer"


async def test_llm_summarizer_wraps_engine_prompt() -> None:
    from mas.library.standard.plugins.context.summarizer import SUMMARIZE_INSTRUCTIONS

    seen: list[list[dict]] = []

    class _RawEngine:
        def summarize_messages(self, messages):
            seen.append(messages)
            return "ok"

    plugin = LlmSummarizer()
    plugin.bind_engine(_RawEngine())
    assert await settle(plugin.summarize([{"role": "user", "content": "hi"}])) == "ok"
    assert seen[0][0] == {"role": "system", "content": SUMMARIZE_INSTRUCTIONS}
    assert seen[0][1]["role"] == "user"


def test_unknown_summarizer_type_fails() -> None:
    with pytest.raises(KeyError, match="spec.summarizer"):
        CMFactory.create(
            spec={"type": "summarising", "params": {"summarizer": "not-a-plugin"}},
        )


class _EngineWithModel:
    model = "gpt-4o"

    def __init__(self) -> None:
        self.seen: list[tuple[list, str | None]] = []

    def summarize_messages(self, messages, *, model=None):
        self.seen.append((messages, model))
        return f"sum:{model or self.model}:{len(messages)}"


def test_llm_summarizer_defaults_to_agent_model(caplog: pytest.LogCaptureFixture) -> None:
    engine = _EngineWithModel()
    with caplog.at_level("INFO"):
        cm = CMFactory.create(
            spec={"type": "summarising", "params": {"keep_turns": 1, "summarizer": "llm"}},
            engine=engine,
        )
    assert cm._summarizer.model == "gpt-4o"
    assert cm._summarizer.model_source == "agent"
    assert any("summarizer llm: model=gpt-4o" in rec.message for rec in caplog.records)


async def test_llm_summarizer_raw_model_override() -> None:
    engine = _EngineWithModel()
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {
                "keep_turns": 1,
                "hysteresis_ratio": 0,
                "summarizer": {"type": "llm", "params": {"model": "gpt-4o-mini"}},
            },
        },
        engine=engine,
    )
    assert cm._summarizer.model == "gpt-4o-mini"
    assert cm._summarizer.model_source == "override"
    past = [
        {"role": "user", "content": "old " + "x" * 80},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "b"},
    ]
    out = await settle(cm.manage_history(past, budget_tokens=1))
    assert "gpt-4o-mini" in out[0]["content"]
    assert engine.seen[0][1] == "gpt-4o-mini"
    assert cm.last_compaction_metadata["model"] == "gpt-4o-mini"
    assert cm.last_compaction_metadata["threshold"] == 1


def test_llm_summarizer_top_level_model_shorthand() -> None:
    engine = _EngineWithModel()
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {"summarizer": {"type": "llm", "model": "haiku"}},
        },
        engine=engine,
    )
    assert cm._summarizer.model == "haiku"


def test_omitted_summarizer_type_keeps_params_model() -> None:
    engine = _EngineWithModel()
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {"summarizer": {"params": {"model": "gpt-4o-mini"}}},
        },
        engine=engine,
    )
    assert isinstance(cm._summarizer, LlmSummarizer)
    assert cm._summarizer.model == "gpt-4o-mini"


def test_llm_summarizer_resolves_models_id() -> None:
    engine = _EngineWithModel()
    manifest = {
        "spec": {
            "models": [
                {"id": "main", "model": "gpt-4o"},
                {"id": "summarizer", "model": "gpt-4o-mini"},
            ],
            "context_manager": {
                "type": "summarising",
                "params": {
                    "keep_turns": 1,
                    "summarizer": {"type": "llm", "params": {"model": "summarizer"}},
                },
            },
        }
    }
    cm = CMFactory.create(manifest=manifest, engine=engine)
    assert cm._summarizer.model == "gpt-4o-mini"
    assert "spec.models[id=summarizer]" in cm._summarizer.model_source


def test_llm_summarizer_uses_experiment_summarizer_slot() -> None:
    engine = _EngineWithModel()
    engine.model_slots = {"summarizer": "gpt-4o-mini"}
    cm = CMFactory.create(
        spec={"type": "summarising", "params": {"keep_turns": 1, "summarizer": "llm"}},
        engine=engine,
    )
    assert cm._summarizer.model == "gpt-4o-mini"
    assert cm._summarizer.model_source == "experiment.models.summarizer"


def test_llm_summarizer_params_model_beats_experiment_slot() -> None:
    engine = _EngineWithModel()
    engine.model_slots = {"summarizer": "gpt-4o-mini"}
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {"summarizer": {"type": "llm", "params": {"model": "haiku"}}},
        },
        engine=engine,
    )
    assert cm._summarizer.model == "haiku"


async def test_compaction_logs_threshold_and_model(caplog: pytest.LogCaptureFixture) -> None:
    engine = _EngineWithModel()
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {
                "keep_turns": 1,
                "hysteresis_ratio": 0,
                "summarizer": {"type": "llm", "model": "gpt-4o-mini"},
            },
        },
        engine=engine,
    )
    past = [
        {"role": "user", "content": "old " + "x" * 80},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "b"},
    ]
    with caplog.at_level("INFO"):
        await settle(cm.manage_history(past, budget_tokens=1))
    assert any("compacted" in rec.message and "gpt-4o-mini" in rec.message for rec in caplog.records)
    assert any("estimated_tokens=" in rec.message for rec in caplog.records)


def test_drop_summarizer_does_not_receive_model() -> None:
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {
                "summarizer": {"type": "drop", "model": "gpt-4o-mini"},
            },
        },
    )
    assert isinstance(cm._summarizer, DropSummarizer)


async def test_llm_summarizer_falls_back_when_engine_rejects_model_kw() -> None:
    class _EngineNoModelKw:
        model = "gpt-4o"

        def summarize_messages(self, messages):
            return f"legacy:{len(messages)}"

    plugin = LlmSummarizer(model="gpt-4o-mini")
    plugin.bind_engine(_EngineNoModelKw(), model="gpt-4o-mini", model_source="override")
    text = await settle(plugin.summarize([{"role": "user", "content": "hi"}]))
    assert text == "legacy:2"


async def test_under_budget_does_not_call_summarizer() -> None:
    engine = _EngineWithModel()
    cm = CMFactory.create(
        spec={
            "type": "summarising",
            "params": {"keep_turns": 1, "summarizer": {"type": "llm", "model": "gpt-4o-mini"}},
        },
        engine=engine,
    )
    past = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "b"},
    ]
    out = await settle(cm.manage_history(past, budget_tokens=10_000))
    assert out == past
    assert engine.seen == []
