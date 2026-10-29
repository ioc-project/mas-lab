#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for LlmCacheMiddleware serialization and multi-turn replay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.engine.infra_pipeline import apply_middleware
from mas.runtime.engine.llm_cache import middleware_cache_deserialize, middleware_cache_serialize
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec
from mas.runtime.schema.observability import ObsEventKind


@dataclass
class _SequenceEngine:
    preview_text: str
    responses: list[EngineIoReturn]
    calls: int = 0
    _index: int = 0

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return self.preview_text if op == "LLM_CALL" else ""

    def reset_turn_state(self) -> None:
        return None

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        ret = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        return ret.model_copy(update={"correlation_id": io.correlation_id})


@dataclass
class _TrajectoryEngine:
    """Simulates two LLM_CALL turns: tool request then final answer."""

    previews: list[str]
    calls: int = 0
    preview_calls: int = 0
    _turn: int = 0

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        if op != "LLM_CALL":
            return ""
        idx = min(self.preview_calls, len(self.previews) - 1)
        self.preview_calls += 1
        return self.previews[idx]

    def reset_turn_state(self) -> None:
        return None

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self.calls += 1
        if self._turn == 0:
            self._turn += 1
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="lookup",
                tool_arguments={"q": "POTUS"},
                text="",
                finish_reason="tool_calls",
            )
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text="The current president is cached.",
            finish_reason="stop",
        )


def test_middleware_cache_roundtrip_tool_call():
    ret = EngineIoReturn(
        correlation_id=1,
        response_kind="MODEL_TEXT",
        next_step="TOOL_CALL",
        tool_name="search",
        tool_arguments={"q": "hello"},
        text="",
        finish_reason="tool_calls",
    )
    entry = middleware_cache_serialize(ret)
    restored = middleware_cache_deserialize(entry, 9)
    assert restored.next_step == "TOOL_CALL"
    assert restored.tool_name == "search"
    assert restored.tool_arguments == {"q": "hello"}


def test_middleware_cache_roundtrip_text_preserves_provider_usage():
    ret = EngineIoReturn(
        correlation_id=1,
        response_kind="MODEL_TEXT",
        next_step="STOP",
        text="answer",
        usage={"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
    )
    entry = middleware_cache_serialize(ret)
    assert isinstance(entry, dict)
    restored = middleware_cache_deserialize(entry, 9)
    assert restored.text == "answer"
    assert restored.usage == ret.usage


def test_middleware_cache_roundtrip_parallel_tools():
    ret = EngineIoReturn(
        correlation_id=1,
        response_kind="MODEL_TEXT",
        next_step="PARALLEL_TOOL_CALLS",
        parallel_tools=(
            ToolCallSpec(tool_name="a", tool_arguments={"x": 1}),
            ToolCallSpec(tool_name="b", tool_arguments={"y": 2}),
        ),
        text="",
    )
    entry = middleware_cache_serialize(ret)
    restored = middleware_cache_deserialize(entry, 3)
    assert restored.next_step == "PARALLEL_TOOL_CALLS"
    assert len(restored.parallel_tools) == 2
    assert restored.parallel_tools[0].tool_name == "a"


def test_llm_cache_middleware_caches_tool_call_response(tmp_path):
    cache_path = tmp_path / "cache.json"
    inner = _SequenceEngine(
        preview_text="[user]\n  Who is POTUS?",
        responses=[
            EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="lookup",
                tool_arguments={"topic": "POTUS"},
                text="",
            )
        ],
    )
    engine = apply_middleware(
        inner,
        {"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}},
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    first = engine.invoke(io)
    second = engine.invoke(io)
    assert first.next_step == "TOOL_CALL"
    assert first.tool_name == "lookup"
    assert first.cache_status == "miss"
    assert first.cache_layer == "infra_llm_cache"
    assert first.cache_events == [{"layer": "infra_llm_cache", "status": "miss"}]
    assert second.next_step == "TOOL_CALL"
    assert second.tool_name == "lookup"
    assert second.cache_status == "hit"
    assert second.cache_layer == "infra_llm_cache"
    assert second.cache_events == [{"layer": "infra_llm_cache", "status": "hit"}]
    assert inner.calls == 1
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert any(isinstance(v, dict) and v.get("next_step") == "TOOL_CALL" for v in data.values())


def test_llm_cache_trajectory_replay_is_all_hits(tmp_path):
    cache_path = tmp_path / "cache.json"
    previews = [
        "[user]\n  Who is POTUS?",
        "[user]\n  Who is POTUS?\n[tool call_id=call_1]\n  Biden",
    ]
    record = apply_middleware(
        _TrajectoryEngine(previews=previews),
        {
            "middleware": "llm_cache",
            "params": {"cache_path": str(cache_path), "allow_read": False, "allow_write": True},
        },
    )
    io1 = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    io2 = InvokeEngineIo(correlation_id=2, op="LLM_CALL")
    record.invoke(io1)
    record.invoke(io2)
    assert len(json.loads(cache_path.read_text(encoding="utf-8"))) == 2

    replay_inner = _TrajectoryEngine(previews=previews)
    replay = apply_middleware(
        replay_inner,
        {
            "middleware": "llm_cache",
            "params": {
                "cache_path": str(cache_path),
                "allow_read": True,
                "allow_write": False,
                "raise_on_miss": True,
            },
        },
    )
    r1 = replay.invoke(io1)
    r2 = replay.invoke(io2)
    assert r1.next_step == "TOOL_CALL"
    assert r2.next_step == "STOP"
    assert r2.text == "The current president is cached."
    assert r1.cache_status == r2.cache_status == "hit"
    assert r1.cache_layer == r2.cache_layer == "infra_llm_cache"
    assert replay_inner.calls == 0


def test_llm_cache_include_preview_stores_exchange_text(tmp_path):
    cache_path = tmp_path / "cache.json"
    preview = "[user]\n  hello"
    inner = _SequenceEngine(
        preview_text=preview,
        responses=[
            EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="hi there",
            )
        ],
    )
    engine = apply_middleware(
        inner,
        {
            "middleware": "llm_cache",
            "params": {"cache_path": str(cache_path), "include_preview": True},
        },
    )
    engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    entry = next(iter(json.loads(cache_path.read_text(encoding="utf-8")).values()))
    assert isinstance(entry, dict)
    assert entry["_preview"] == preview


def test_llm_cache_backward_compatible_plain_string_entries(tmp_path):
    import hashlib

    cache_path = tmp_path / "cache.json"
    preview = "echo preview"
    key = hashlib.sha256(preview.encode()).hexdigest()
    cache_path.write_text(json.dumps({key: "plain answer"}), encoding="utf-8")
    inner = _SequenceEngine(
        preview_text=preview,
        responses=[EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="live")],
    )
    engine = apply_middleware(
        inner,
        {"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}},
    )
    ret = engine.invoke(InvokeEngineIo(correlation_id=5, op="LLM_CALL"))
    assert ret.text == "plain answer"
    assert ret.next_step == "STOP"
    assert inner.calls == 0


def test_raise_on_miss_records_cache_lookup_without_llm_return(tmp_path):
    cache_path = tmp_path / "cache.json"
    inner = _SequenceEngine(
        preview_text="not recorded",
        responses=[EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP")],
    )
    observability = ObservabilityOperator()
    inner.ctx = SimpleNamespace(observability=observability)
    engine = apply_middleware(
        inner,
        {
            "middleware": "llm_cache",
            "params": {
                "cache_path": str(cache_path),
                "allow_read": True,
                "allow_write": False,
                "raise_on_miss": True,
            },
        },
    )
    with pytest.raises(RuntimeError, match="llm_cache miss"):
        engine.invoke(InvokeEngineIo(correlation_id=7, op="LLM_CALL"))
    event = next(event for event in observability.events if event.kind == ObsEventKind.CACHE_LOOKUP)
    assert event.correlation_id == 7
    assert event.payload["cache_layer"] == "infra_llm_cache"
    assert event.payload["cache_status"] == "miss"


def test_peer_engine_middleware_instances_do_not_clobber_cache(tmp_path):
    """MAS peer agents each wrap LiveLlmEngine; persist must keep both keys."""
    cache_path = tmp_path / "cache.json"
    inner_a = _SequenceEngine(
        preview_text="moderator",
        responses=[
            EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="delegate_to_itinerary_agent",
                tool_arguments={"task": "routes"},
                text="",
            )
        ],
    )
    inner_b = _SequenceEngine(
        preview_text="itinerary",
        responses=[
            EngineIoReturn(
                correlation_id=1,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="Celestia → Verdantia",
            )
        ],
    )
    spec = {"middleware": "llm_cache", "params": {"cache_path": str(cache_path)}}
    apply_middleware(inner_a, spec).invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    apply_middleware(inner_b, spec).invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(data) == 2
