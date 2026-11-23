#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Per-event-kind handlers, dispatched by ``mas_class`` from
``core.native_graph_builder.NativeGraphBuilder.process_event``.

Mirrors the reference OTel normalizer's ``_HANDLERS`` dispatch table
(``norm.ioa_observe.build``): one small, focused function per node family,
each reading attributes straight off one event and adding the nodes and edges
that event says to the builder (a merge by id).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .agent import handle_agent_call
from .annotation import handle_annotation, handle_context_contribution
from .chat import handle_llm_call, handle_thinking_call
from .memory import handle_memory_call
from .processing import handle_processing_call
from .session import handle_mas_call
from .structural import handle_worker
from .tool import handle_skill_call, handle_tool_call
from .transport import handle_transport

HandlerFn = Callable[[Dict[str, Any], Any], List[Dict[str, Any]]]

# mas_class (ontology local class name, from core.event_mappings.KIND_TO_CLASS
# / core.graph_builder._class_for_event) -> handler function.
HANDLERS: Dict[str, HandlerFn] = {
    "MASCall": handle_mas_call,
    "AgentCall": handle_agent_call,
    "LLMCall": handle_llm_call,
    "ThinkingCall": handle_thinking_call,
    "ToolCall": handle_tool_call,
    "SkillCall": handle_skill_call,
    "ProcessingCall": handle_processing_call,
    "RAGQuery": handle_memory_call,
    "MemoryCall": handle_memory_call,
    "CallAnnotation": handle_annotation,
    "GovernanceEvent": handle_annotation,
    "ContextContribution": handle_context_contribution,
    "Worker": handle_worker,
    "Agent": handle_transport,
}

__all__ = ["HANDLERS"]
