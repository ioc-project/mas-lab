#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native path emits node types that exist on scope_ontology.models when present."""

from __future__ import annotations

import pytest

from mas.library.kg.core.event_mappings import KIND_TO_CLASS
from mas.library.kg.core.scope_models import NATIVE_EXTENSION_NODE_TYPES, SCOPE_CORE_NODE_TYPES
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


def test_every_mapped_class_is_a_scope_model_or_a_declared_extension() -> None:
    mapped = {cls for cls in KIND_TO_CLASS.values() if cls}
    undeclared = mapped - SCOPE_CORE_NODE_TYPES - NATIVE_EXTENSION_NODE_TYPES
    assert not undeclared, undeclared


def test_native_core_types_exist_on_scope_models(native_doc) -> None:
    from scope_ontology import models as scope_models

    present = {n.get("node_type") for n in native_doc["nodes"]}
    core_emitted = present & SCOPE_CORE_NODE_TYPES
    assert {"AgentCall", "LLMCall", "ToolCall", "Session"} <= core_emitted
    for ntype in core_emitted:
        assert hasattr(scope_models, ntype), f"scope_ontology.models missing {ntype}"


def test_native_nodes_carry_an_id_and_a_scope_type(native_doc) -> None:
    for node in native_doc["nodes"]:
        assert node.get("node_type") in SCOPE_CORE_NODE_TYPES, node
        assert node.get("id"), node
