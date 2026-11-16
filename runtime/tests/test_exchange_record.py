#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Driver ExchangeRecord is structured data, not a pretty-printed dump."""

from __future__ import annotations

from types import SimpleNamespace

from mas.runtime.driver.driver import _engine_invoke_record, engine_manifest, engine_model_id
from mas.runtime.engine.exchange_preview import ExchangeSnapshot
from mas.runtime.schema.egress import InvokeEngineIo


class _SnapshotEngine:
    model = "gpt-4o"

    def exchange_snapshot(self, op: str, *, correlation_id: int = 0) -> ExchangeSnapshot:
        assert op == "LLM_CALL"
        return ExchangeSnapshot(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who is POTUS?"},
            ],
            tools=[{"type": "function", "function": {"name": "web-search"}}],
        )


async def test_llm_request_record_keeps_messages_not_pretty_print():
    q = SimpleNamespace(pending_tools_by_cid={}, pending_tool_name="", pending_tool_args={})
    rec, tracked = await _engine_invoke_record(
        InvokeEngineIo(correlation_id=1, op="LLM_CALL"),
        engine=_SnapshotEngine(),
        q=q,
        agent_id="qa",
        ts_mono=0.0,
        ts_wall="",
        engine_raw="",
    )
    assert rec.kind == "llm_request"
    assert tracked == ""
    assert rec.messages is not None
    assert rec.messages[0]["role"] == "system"
    assert rec.tools is not None
    assert rec.model == "gpt-4o"
    assert rec.text == ""
    assert "[system]" not in rec.text
    assert "[tools]" not in rec.text


async def test_tool_call_record_keeps_name_and_arguments():
    q = SimpleNamespace(
        pending_tools_by_cid={2: ("web-search", {"query": "current POTUS"})},
        pending_tool_name="",
        pending_tool_args={},
    )
    rec, tracked = await _engine_invoke_record(
        InvokeEngineIo(correlation_id=2, op="TOOL_CALL"),
        engine=None,
        q=q,
        agent_id="qa",
        ts_mono=0.0,
        ts_wall="",
        engine_raw="",
    )
    assert rec.kind == "tool_call"
    assert tracked == "web-search"
    assert rec.tool_name == "web-search"
    assert rec.tool_arguments == {"query": "current POTUS"}
    assert rec.semantics is None
    assert rec.text == ""
    assert "tool=" not in rec.text
    assert "args=" not in rec.text


async def test_tool_call_does_not_infer_skill_from_name():
    q = SimpleNamespace(
        pending_tools_by_cid={3: ("activate_skill", {"name": "answer-formatting"})},
        pending_tool_name="",
        pending_tool_args={},
    )
    rec, tracked = await _engine_invoke_record(
        InvokeEngineIo(correlation_id=3, op="TOOL_CALL"),
        engine=None,
        q=q,
        agent_id="qa",
        ts_mono=0.0,
        ts_wall="",
        engine_raw="",
    )
    assert tracked == "activate_skill"
    assert rec.tool_name == "activate_skill"
    assert rec.semantics is None


class _AdvertiseEngine:
    model = "gpt-4o"
    ctx = None

    def __init__(self, specs: list[dict]) -> None:
        self.tool_provider = SimpleNamespace(list_tools=lambda ctx=None: specs)


async def test_tool_call_binds_advertised_semantics():
    q = SimpleNamespace(
        pending_tools_by_cid={4: ("activate_skill", {"name": "answer-formatting"})},
        pending_tool_name="",
        pending_tool_args={},
    )
    engine = _AdvertiseEngine(
        [
            {
                "name": "activate_skill",
                "semantics": {"concept": "skill", "op": "activate", "subject_arg": "name"},
            }
        ]
    )
    rec, tracked = await _engine_invoke_record(
        InvokeEngineIo(correlation_id=4, op="TOOL_CALL"),
        engine=engine,
        q=q,
        agent_id="qa",
        ts_mono=0.0,
        ts_wall="",
        engine_raw="",
    )
    assert tracked == "activate_skill"
    assert rec.semantics == {
        "concept": "skill",
        "op": "activate",
        "subject": "answer-formatting",
    }


def test_engine_model_id_unwraps_inner():
    inner = SimpleNamespace(model="gpt-4o", inner=None)
    outer = SimpleNamespace(model="", inner=inner)
    assert engine_model_id(outer) == "gpt-4o"
    assert engine_model_id(None) == ""
    assert engine_model_id(SimpleNamespace()) == ""


def test_engine_model_id_unwraps_getattr_forwarding_wrapper():
    class Wrapper:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    wrapped = Wrapper(SimpleNamespace(model="gpt-4o"))
    assert engine_model_id(wrapped) == "gpt-4o"


def test_engine_model_id_does_not_follow_mock_inner_forever():
    from unittest.mock import MagicMock

    engine = MagicMock()
    assert engine_model_id(engine) == ""
    # getattr() on MagicMock would have created these children.
    assert "inner" not in engine._mock_children
    assert "model" not in engine._mock_children


def test_engine_model_id_mock_does_not_grow_with_repeated_calls():
    from unittest.mock import MagicMock

    engine = MagicMock()
    for _ in range(1000):
        assert engine_model_id(engine) == ""
    assert engine._mock_children == {}


def test_engine_manifest_unwraps_inner():
    spec = {"spec": {"context_manager": "summarising"}}
    inner = SimpleNamespace(manifest=spec, inner=None)
    outer = SimpleNamespace(manifest=None, inner=inner)
    assert engine_manifest(outer) is spec
    assert engine_manifest(None) is None
    assert engine_manifest(SimpleNamespace()) is None
