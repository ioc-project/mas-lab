#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ioa-observe / GenAI semantic-convention attribute names.

Names that ``ioa_observe.sdk.utils.const`` defines are read from it (``_const``,
by the SDK's own constant name) when the installed SDK has them; the literal next
to each is the same published value, used when the installed SDK predates that
constant. Names the SDK does not define (yet) are plain literals here. Every
other module takes these names from this one.
"""

from __future__ import annotations

from typing import Final

try:
    from ioa_observe.sdk.utils import const as _sdk_const  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - the SDK is a dependency; kept for a bare install
    _sdk_const = None


def _const(name: str, published: str) -> str:
    """``ioa_observe.sdk.utils.const.<name>`` when the installed SDK has it, else its published value."""
    return str(getattr(_sdk_const, name, published) or published)


APPLICATION_ID: Final[str] = "application_id"
AGENT_ID: Final[str] = "agent_id"
SESSION_ID: Final[str] = "session.id"
SESSION_NAME: Final[str] = "session.name"
IOA_START_TIME: Final[str] = "ioa_start_time"
IOA_SPAN_KIND: Final[str] = _const("OBSERVE_SPAN_KIND", "ioa_observe.span.kind")
IOA_ENTITY_NAME: Final[str] = _const("OBSERVE_ENTITY_NAME", "ioa_observe.entity.name")
IOA_ENTITY_INPUT: Final[str] = _const("OBSERVE_ENTITY_INPUT", "ioa_observe.entity.input")
IOA_ENTITY_OUTPUT: Final[str] = _const("OBSERVE_ENTITY_OUTPUT", "ioa_observe.entity.output")
IOA_AGENT_SPAN_ID: Final[str] = _const("OBSERVE_AGENT_SPAN_ID", "ioa_observe.agent.span_id")
IOA_AGENT_TRACE_ID: Final[str] = _const("OBSERVE_AGENT_TRACE_ID", "ioa_observe.agent.trace_id")
IOA_AGENT_SEQUENCE: Final[str] = _const("OBSERVE_AGENT_SEQUENCE", "ioa_observe.agent.sequence")
IOA_WORKFLOW_NAME: Final[str] = _const("OBSERVE_WORKFLOW_NAME", "ioa_observe.workflow.name")
AGENT_CHAIN_START_TIME: Final[str] = "agent_chain_start_time"
SPAN_NAME_AGENT_START_EVENT: Final[str] = "agent_start_event"
SPAN_NAME_AGENT_END_EVENT: Final[str] = "agent_end_event"
EXECUTION_SUCCESS: Final[str] = "execution.success"
# A call status that means success; every other status is a failure. The native KG fold
# (``mas.library.kg.core.native_graph_builder.SUCCESS_STATUS``) reads the same set.
SUCCESS_STATUS: Final[frozenset[str]] = frozenset({"ok", "success", "true", "completed"})


# The fields of an annotation record that no ontology property holds: its ``payload``,
# written as one JSON object under the record's own field names
# (``mas.annotation.content``). The native KG fold
# (``mas.library.kg.observability.native.handlers.annotation``) writes the same payload.
ANNOTATION_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "source_agent_id",
    "target_agent_id",
    "task",
    "correlation_id",
    "status",
    "segments",
    "total_tokens",
    "operation",
    "target",
    "from",
    "to",
    "message_type",
    "denied_call_id",
    "budget_scope",
    "amount",
    "intervention_type",
)
CONTEXT_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "parents",
    "section_id",
    "source_type",
    "cause",
    "cause_type",
    "sensitivity",
    "eviction_reason",
    "content",
)


def annotation_payload(ev: dict, fields: tuple[str, ...]) -> str:
    """The payload of an annotation record: the ``fields`` it carries, as JSON ("" if none)."""
    import json

    values = {key: ev[key] for key in fields if ev.get(key) not in (None, "", [])}
    return json.dumps(values, sort_keys=True, separators=(",", ":"), default=str) if values else ""


def execution_success(status: object) -> str:
    """``execution.success`` of a call that ended with ``status`` (none reads as success)."""
    return "true" if str(status or "success").strip().lower() in SUCCESS_STATUS else "false"


GEN_AI_PROVIDER: Final[str] = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL: Final[str] = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE: Final[str] = "gen_ai.request.temperature"
GEN_AI_OPERATION_NAME: Final[str] = "gen_ai.operation.name"
GEN_AI_PROMPT_ROLE: Final[str] = "gen_ai.prompt.0.role"
GEN_AI_PROMPT_CONTENT: Final[str] = "gen_ai.prompt.0.content"
GEN_AI_COMPLETION_ROLE: Final[str] = "gen_ai.completion.0.role"
GEN_AI_COMPLETION_CONTENT: Final[str] = "gen_ai.completion.0.content"
GEN_AI_INPUT_MESSAGES: Final[str] = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES: Final[str] = "gen_ai.output.messages"
GEN_AI_USAGE_INPUT_TOKENS: Final[str] = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final[str] = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS: Final[str] = "gen_ai.usage.total_tokens"
GEN_AI_FINISH_REASONS: Final[str] = "gen_ai.response.finish_reasons"
GEN_AI_TOOL_ARGUMENTS: Final[str] = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_RESULT: Final[str] = "gen_ai.tool.call.result"
GEN_AI_IOA_GRAPH: Final[str] = "gen_ai.ioa.graph"
GEN_AI_IOA_GRAPH_PROTOCOL: Final[str] = "gen_ai.ioa.graph.protocol"
GEN_AI_IOA_GRAPH_DYNAMISM: Final[str] = "gen_ai.ioa.graph_dynamism"
GEN_AI_IOA_GRAPH_DETERMINISM: Final[str] = "gen_ai.ioa.graph_determinism_score"
OBSERVE_MEMORY_OPERATION: Final[str] = _const("OBSERVE_MEMORY_OPERATION", "ioa_observe.memory.operation")
OBSERVE_MEMORY_TYPE: Final[str] = _const("OBSERVE_MEMORY_TYPE", "ioa_observe.memory.type")
OBSERVE_PROCESSING_TYPE: Final[str] = _const("OBSERVE_PROCESSING_TYPE", "ioa_observe.processing.type")
OBSERVE_PROCESSING_ACTOR: Final[str] = _const("OBSERVE_PROCESSING_ACTOR", "ioa_observe.processing.actor")
OBSERVE_GOVERNANCE_DECISION_TYPE: Final[str] = _const(
    "OBSERVE_GOVERNANCE_DECISION_TYPE", "ioa_observe.governance.decision_type"
)
OBSERVE_GOVERNANCE_POLICY_ID: Final[str] = _const("OBSERVE_GOVERNANCE_POLICY_ID", "ioa_observe.governance.policy_id")
# Mirrors of observe-sdk const.py OBSERVE_PROCESSING_NAME and the explicit dataflow
# OBSERVE_STATE_{INPUT,OUTPUT}_{ID,CONTENT}.
OBSERVE_PROCESSING_NAME: Final[str] = _const("OBSERVE_PROCESSING_NAME", "ioa_observe.processing.name")
OBSERVE_STATE_INPUT_ID: Final[str] = _const("OBSERVE_STATE_INPUT_ID", "ioa_observe.state.input.id")
OBSERVE_STATE_INPUT_CONTENT: Final[str] = _const("OBSERVE_STATE_INPUT_CONTENT", "ioa_observe.state.input.content")
OBSERVE_STATE_OUTPUT_ID: Final[str] = _const("OBSERVE_STATE_OUTPUT_ID", "ioa_observe.state.output.id")
OBSERVE_STATE_OUTPUT_CONTENT: Final[str] = _const("OBSERVE_STATE_OUTPUT_CONTENT", "ioa_observe.state.output.content")
# The execution an annotation, governance or context record is about (norm
# ``handlers/annotation.py``). Not an SDK constant.
OBSERVE_ANNOTATES_SPAN_ID: Final[str] = "ioa_observe.annotates.span_id"
# Reasoning on an LLM span. Not fixed by the GenAI semantic conventions yet:
# these are the names norm reads (``norm.ioa_observe.fields``).
GEN_AI_USAGE_REASONING_TOKENS: Final[str] = "gen_ai.usage.reasoning_tokens"
GEN_AI_REQUEST_REASONING_EFFORT: Final[str] = "gen_ai.request.reasoning_effort"
# Ontology memory operations (scope ``MemoryCall.memoryOperation``).
MEMORY_OPERATION_STORE: Final[str] = "store"
MEMORY_OPERATION_RETRIEVE: Final[str] = "retrieve"
MEMORY_OPERATION_UPDATE: Final[str] = "update"
MEMORY_OPERATION_DELETE: Final[str] = "delete"
SPAN_SUFFIX_AGENT: Final[str] = _const("OBSERVE_SPAN_SUFFIX_AGENT", "agent")
SPAN_SUFFIX_CHAT: Final[str] = _const("OBSERVE_SPAN_SUFFIX_CHAT", "chat")
SPAN_SUFFIX_TOOL: Final[str] = _const("OBSERVE_SPAN_SUFFIX_TOOL", "tool")
SPAN_SUFFIX_GRAPH: Final[str] = _const("OBSERVE_SPAN_SUFFIX_GRAPH", "graph")
SPAN_SUFFIX_MEMORY: Final[str] = _const("OBSERVE_SPAN_SUFFIX_MEMORY", "memory")
SPAN_SUFFIX_PROCESSING: Final[str] = _const("OBSERVE_SPAN_SUFFIX_PROCESSING", "processing")
SPAN_SUFFIX_CONTEXT: Final[str] = _const("OBSERVE_SPAN_SUFFIX_CONTEXT", "context")
SPAN_SUFFIX_GOVERNANCE: Final[str] = _const("OBSERVE_SPAN_SUFFIX_GOVERNANCE", "governance")
SPAN_SUFFIX_SKILL: Final[str] = _const("OBSERVE_SPAN_SUFFIX_SKILL", "skill")
SPAN_SUFFIX_RAG: Final[str] = _const("OBSERVE_SPAN_SUFFIX_RAG", "rag")
# Not ioa-observe suffixes: norm reads ``*.thinking``, ``*.worker`` and ``*.annotation``.
SPAN_SUFFIX_THINKING: Final[str] = "thinking"
SPAN_SUFFIX_WORKER: Final[str] = "worker"
SPAN_SUFFIX_ANNOTATION: Final[str] = "annotation"

# TaskCall is the MAS-run wrapper. Observe-sdk names the equivalent span
# ``invoke_agent {graph}`` (e.g. ``invoke_agent LangGraph``). SCOPE ``norm``
# ignores it; Inspect still shows it as the workflow root.
SPAN_NAME_TASK_CALL: Final[str] = "invoke_agent mas"
SPAN_NAME_SESSION_START: Final[str] = "session.start"
SPAN_NAME_SESSION_END: Final[str] = "session.end"


def invoke_agent_span_name(app_name: str | None) -> str:
    """Observe-sdk workflow root: ``invoke_agent LangGraph`` → ``invoke_agent {app}``."""
    return f"invoke_agent {str(app_name or 'mas').strip() or 'mas'}"


def session_id_for(application_id: str, raw_session_id: str) -> str:
    """SCOPE ``fields.get_session_id`` strips an ``<application_id>_`` prefix."""
    app = (application_id or "").strip()
    sid = (raw_session_id or "").strip()
    if not sid:
        return ""
    if app and sid.startswith(f"{app}_"):
        return sid
    if app:
        return f"{app}_{sid}"
    return sid


def wire_span_id(span_id: int | str | None) -> str:
    """16 lowercase hex chars, no ``0x`` — OTLP / ClickHouse / norm ``SpanId``.

    Inspect's Execution Timeline only nests Agent/MAS/Session over calls when
    norm can attach ``hasLLMCall`` / ``hasToolCall``. That lookup is an exact
    string match of ``ioa_observe.agent.span_id`` against ``AgentCall.spanId``,
    which is the exported span id after OTLP normalisation (no prefix).
    """
    if span_id is None or span_id == "":
        return ""
    if isinstance(span_id, int):
        return format(span_id, "016x")
    text = str(span_id).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    return text.zfill(16)[-16:]


def wire_trace_id(trace_id: int | str | None) -> str:
    """32 lowercase hex chars, no ``0x`` — the TraceId counterpart of :func:`wire_span_id`."""
    if trace_id is None or trace_id == "":
        return ""
    if isinstance(trace_id, int):
        return format(trace_id, "032x")
    text = str(trace_id).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    return text.zfill(32)[-32:]


def provider_and_model(model: str | None) -> tuple[str, str]:
    raw = str(model or "").strip()
    if "/" in raw:
        provider, name = raw.split("/", 1)
        return provider or "unknown", _llm_display_name(name)
    return "unknown", _llm_display_name(raw)


def _llm_display_name(name: str) -> str:
    cleaned = str(name or "").strip()
    if not cleaned or cleaned.lower() in {"unknown", "none", "null", "n/a"}:
        # Inspect colors a call as ``llm`` when the entity name contains
        # "llm" (or a vendor). Cached traces often omit the model.
        return "llm"
    return cleaned
