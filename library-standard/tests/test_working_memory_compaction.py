#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""spec.working_memory.compaction facade over context_manager/CMFactory."""

import json

import pytest
from mas.library.standard.lib.context.compaction import (
    apply_working_memory_compaction,
    build_llm_summarize_fn,
    context_manager_binding_from_compaction,
    resolve_working_memory_context_manager,
)
from mas.library.standard.plugins.context.conversation import (
    SlidingWindowConversation,
    StackConversation,
    SummarizingConversation,
)
from mas.library.standard.plugins.context.summarizer import SUMMARIZE_INSTRUCTIONS, LlmSummarizer
from mas.runtime.contracts.cm_factory import CMFactory


def test_keep_recent_translates_to_stack_type_and_max_messages():
    binding = context_manager_binding_from_compaction({"strategy": "keep_recent", "max_messages": 50})
    assert binding == {"type": "stack", "params": {"max_messages": 50}}


def test_default_strategy_is_keep_recent():
    binding = context_manager_binding_from_compaction({})
    assert binding["type"] == "stack"


def test_sliding_window_translates_window_size():
    binding = context_manager_binding_from_compaction({"strategy": "sliding_window", "window_size": 20})
    assert binding == {"type": "sliding_window", "params": {"window_size": 20}}


def test_summarize_translates_threshold_and_keep_turns():
    binding = context_manager_binding_from_compaction(
        {"strategy": "summarize", "summary_threshold": 8000, "keep_turns": 6}
    )
    assert binding == {
        "type": "summarising",
        "params": {"summary_threshold": 8000, "keep_turns": 6},
    }


def test_summarize_model_becomes_summarizer_params():
    binding = context_manager_binding_from_compaction(
        {"strategy": "summarize", "model": "gpt-4o-mini", "keep_turns": 4}
    )
    assert binding["type"] == "summarising"
    assert binding["params"]["keep_turns"] == 4
    assert binding["params"]["summarizer"] == {
        "type": "llm",
        "params": {"model": "gpt-4o-mini"},
    }


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="unknown working_memory.compaction.strategy"):
        context_manager_binding_from_compaction({"strategy": "bogus"})


def test_resolve_returns_none_without_working_memory():
    assert resolve_working_memory_context_manager({}) is None


def test_resolve_returns_none_without_compaction_block():
    assert resolve_working_memory_context_manager({"working_memory": {"persistent": True}}) is None


def test_explicit_context_manager_takes_precedence_over_compaction():
    spec = {
        "context_manager": {"type": "sliding_window", "params": {"window_size": 3}},
        "working_memory": {"compaction": {"strategy": "keep_recent", "max_messages": 999}},
    }
    assert resolve_working_memory_context_manager(spec) is None


def test_resolve_translates_compaction_when_no_explicit_context_manager():
    spec = {"working_memory": {"compaction": {"strategy": "sliding_window", "window_size": 7}}}
    binding = resolve_working_memory_context_manager(spec)
    assert binding == {"type": "sliding_window", "params": {"window_size": 7}}


class _FakeEngine:
    def summarize_messages(self, messages):
        return f"summary of {len(messages)} prompt messages"


def test_build_llm_summarize_fn_is_engine_completion():
    fn = build_llm_summarize_fn(_FakeEngine())
    result = fn([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert result == "summary of 2 prompt messages"


def test_llm_summarizer_wraps_then_engine_completes():
    seen: list[list[dict]] = []

    class _Capture:
        def summarize_messages(self, messages):
            seen.append(messages)
            return "ok"

    plugin = LlmSummarizer()
    plugin.bind_engine(_Capture())
    result = plugin.summarize([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert result == "ok"
    assert seen[0][0]["content"] == SUMMARIZE_INSTRUCTIONS
    assert json.loads(seen[0][1]["content"]) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_apply_working_memory_compaction_wires_a_real_context_manager():
    spec = {"working_memory": {"compaction": {"strategy": "keep_recent", "max_messages": 2}}}
    apply_working_memory_compaction(spec)
    assert spec["context_manager"] == {"type": "stack", "params": {"max_messages": 2}}

    cm = CMFactory.create(spec=spec["context_manager"])
    assert isinstance(cm, StackConversation)
    past = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    assert len(cm.manage_history(past, 0)) == 2


def test_apply_working_memory_compaction_summarize_with_live_engine():
    spec = {"working_memory": {"compaction": {"strategy": "summarize", "keep_turns": 3}}}
    apply_working_memory_compaction(spec, engine=_FakeEngine())

    cm_binding = spec["context_manager"]
    assert cm_binding["type"] == "summarising"
    assert cm_binding["params"] == {"keep_turns": 3}
    assert "summarize_fn" not in cm_binding["params"]

    cm = CMFactory.create(spec=cm_binding, engine=_FakeEngine())
    assert isinstance(cm, SummarizingConversation)
    assert cm._summarize_fn is not None


def test_apply_working_memory_compaction_wires_explicit_summarising_context_manager():
    spec = {
        "context_manager": {
            "type": "summarising",
            "params": {"summary_threshold": 1000, "keep_turns": 2},
        }
    }
    apply_working_memory_compaction(spec, engine=_FakeEngine())
    assert spec["context_manager"]["type"] == "summarising"
    assert "summarize_fn" not in (spec["context_manager"].get("params") or {})


def test_apply_working_memory_compaction_summarize_without_engine_keeps_summarising():
    spec = {"working_memory": {"compaction": {"strategy": "summarize"}}}
    apply_working_memory_compaction(spec, engine=None)
    assert spec["context_manager"]["type"] == "summarising"
    assert "summarize_fn" not in spec["context_manager"]["params"]
    cm = CMFactory.create(spec=spec["context_manager"])
    assert isinstance(cm, SummarizingConversation)
    assert cm._summarize_fn is not None


def test_apply_working_memory_compaction_noop_when_nothing_configured():
    spec = {"description": "an agent"}
    apply_working_memory_compaction(spec)
    assert "context_manager" not in spec


def test_sliding_window_binding_instantiates_via_registry():
    spec = {"working_memory": {"compaction": {"strategy": "sliding_window", "window_size": 4}}}
    apply_working_memory_compaction(spec)
    cm = CMFactory.create(spec=spec["context_manager"])
    assert isinstance(cm, SlidingWindowConversation)
    assert cm.max_turns == 4


class _FakeEngineWithBudget(_FakeEngine):
    def __init__(self, *, max_llm_calls):
        from mas.runtime.boundary.gov.budget import BudgetTracker

        self._budget = BudgetTracker(max_llm_calls=max_llm_calls)

    def summarize_messages(self, messages):
        if not self._budget.allow_llm():
            raise RuntimeError("history summarizer: LLM call budget exceeded (spec.budget.max_llm_calls)")
        self._budget.note_llm()
        return super().summarize_messages(messages)


def test_build_llm_summarize_fn_counts_against_engine_budget():
    engine = _FakeEngineWithBudget(max_llm_calls=5)
    fn = build_llm_summarize_fn(engine)
    fn([{"role": "user", "content": "hi"}])
    assert engine._budget.llm_calls == 1


def test_build_llm_summarize_fn_raises_when_budget_exhausted():
    engine = _FakeEngineWithBudget(max_llm_calls=1)
    fn = build_llm_summarize_fn(engine)
    fn([{"role": "user", "content": "hi"}])
    with pytest.raises(RuntimeError, match="budget exceeded"):
        fn([{"role": "user", "content": "again"}])
