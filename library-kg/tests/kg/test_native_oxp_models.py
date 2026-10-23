#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native path emits node types that exist on oxp_ontology.models when present."""

from __future__ import annotations

import pytest

from mas.library.kg.core.event_mappings import KIND_TO_CLASS
from mas.library.kg.core.oxp_models import NATIVE_EXTENSION_NODE_TYPES, OXP_CORE_NODE_TYPES
from mas.library.kg.pipeline import build_kg_document


@pytest.fixture
def native_doc():
    events = [
        {
            "kind": "execution_start",
            "timestamp": 1.0,
            "run_id": "scope-native-1",
            "call_id": "c-agent",
            "agent_id": "planner",
            "input": "hello",
        },
        {
            "kind": "llm_call_start",
            "timestamp": 1.1,
            "run_id": "scope-native-1",
            "call_id": "c-llm",
            "parent_call_id": "c-agent",
            "agent_id": "planner",
            "model": "gpt-4",
        },
        {
            "kind": "llm_call_end",
            "timestamp": 1.5,
            "run_id": "scope-native-1",
            "call_id": "c-llm",
            "parent_call_id": "c-agent",
            "agent_id": "planner",
            "status": "success",
        },
        {
            "kind": "tool_call_start",
            "timestamp": 1.6,
            "run_id": "scope-native-1",
            "call_id": "c-tool",
            "parent_call_id": "c-agent",
            "agent_id": "planner",
            "tool_name": "search",
        },
        {
            "kind": "tool_call_end",
            "timestamp": 1.8,
            "run_id": "scope-native-1",
            "call_id": "c-tool",
            "parent_call_id": "c-agent",
            "agent_id": "planner",
            "tool_name": "search",
            "status": "success",
            "result": {"ok": True},
        },
        {
            "kind": "execution_end",
            "timestamp": 2.0,
            "run_id": "scope-native-1",
            "call_id": "c-agent",
            "agent_id": "planner",
            "status": "success",
            "output": "done",
        },
    ]
    return build_kg_document(events, run_id="scope-native-1")


def test_kind_to_class_core_subset_is_documented() -> None:
    mapped = {cls for cls in KIND_TO_CLASS.values() if cls}
    missing_from_audit = mapped - OXP_CORE_NODE_TYPES - NATIVE_EXTENSION_NODE_TYPES
    assert not missing_from_audit, f"KIND_TO_CLASS has undocumented classes: {missing_from_audit}"


def test_native_core_types_exist_on_oxp_models(native_doc) -> None:
    pytest.importorskip("oxp_ontology")
    from oxp_ontology import models as oxp_models

    present = {n.get("node_type") for n in native_doc["nodes"]}
    core_emitted = present & OXP_CORE_NODE_TYPES
    assert {"AgentCall", "LLMCall", "ToolCall", "Session"} <= core_emitted
    for ntype in core_emitted:
        assert hasattr(oxp_models, ntype), f"oxp_ontology.models missing {ntype}"


def test_native_extension_types_are_typed_dicts_when_models_absent(native_doc) -> None:
    nodes_by_type = {n.get("node_type") for n in native_doc["nodes"]}
    for node in native_doc["nodes"]:
        ntype = node.get("node_type")
        if ntype in NATIVE_EXTENSION_NODE_TYPES:
            assert node.get("@type") == ntype or node.get("node_type") == ntype
        if ntype in OXP_CORE_NODE_TYPES:
            assert node.get("id") or node.get("callId") or node.get("sessionId")
    assert "AgentCall" in nodes_by_type
