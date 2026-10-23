#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ioa-observe / GenAI semantic-convention attribute names.

Prefer constants exported by the installed ``ioa-observe-sdk`` when present.
Fall back to the published string values so conversion works with stock PyPI
SDK, a local extended checkout, or no SDK at all (attribute names only).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Final

logger = logging.getLogger(__name__)


def _load_sdk_span_attributes() -> Any | None:
    for module_name in (
        "ioa_observe.sdk.tracing.tracing",
        "ioa_observe.sdk.tracing",
        "ioa_observe.tracing.tracing",
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        except Exception:
            # The module exists but is broken (e.g. an internal import error
            # inside ioa_observe) -- distinct from "genuinely absent," and
            # worth a log line rather than silently falling back to guessed
            # literal attribute names as if the dependency weren't installed.
            logger.warning(
                "ioa-observe-sdk's %s is installed but failed to import; "
                "falling back to literal attribute-name strings.",
                module_name,
                exc_info=True,
            )
            continue
        attrs = getattr(module, "SpanAttributes", None)
        if attrs is not None:
            return attrs
    return None


_SdkSpanAttributes = _load_sdk_span_attributes()


def _sdk(name: str, default: str) -> str:
    if _SdkSpanAttributes is None:
        return default
    return str(getattr(_SdkSpanAttributes, name, default) or default)


APPLICATION_ID: Final[str] = "application_id"
AGENT_ID: Final[str] = "agent_id"
SESSION_ID: Final[str] = "session.id"
SESSION_NAME: Final[str] = "session.name"
IOA_START_TIME: Final[str] = _sdk("IOA_START_TIME", "ioa_start_time")
IOA_SPAN_KIND: Final[str] = "ioa_observe.span.kind"
IOA_ENTITY_NAME: Final[str] = _sdk("IOA_OBSERVE_ENTITY_NAME", "ioa_observe.entity.name")
IOA_ENTITY_INPUT: Final[str] = _sdk("IOA_OBSERVE_ENTITY_INPUT", "ioa_observe.entity.input")
IOA_ENTITY_OUTPUT: Final[str] = _sdk("IOA_OBSERVE_ENTITY_OUTPUT", "ioa_observe.entity.output")
IOA_AGENT_SPAN_ID: Final[str] = "ioa_observe.agent.span_id"
IOA_AGENT_TRACE_ID: Final[str] = "ioa_observe.agent.trace_id"
IOA_AGENT_PREVIOUS: Final[str] = "ioa_observe.agent.previous"
IOA_AGENT_SEQUENCE: Final[str] = "ioa_observe.agent.sequence"
IOA_HANDOFF_SOURCE_SPAN_IDS: Final[str] = "ioa_observe.handoff.source.span_ids"
# The real SDK emits this as a native OTel array attribute. SCOPE's own
# norm.heuristics.handoff._source_span_ids reads ioa_observe.handoff.source.
# span_ids with json.loads(raw) -- it would raise (caught, returning no
# signal) on a real array value, so both handoff attributes here stay
# JSON-encoded strings, matching what norm actually parses rather than what
# the real SDK happens to emit for this one pair of keys.
IOA_HANDOFF_SOURCE_TRACE_IDS: Final[str] = "ioa_observe.handoff.source.trace_ids"
IOA_WORKFLOW_NAME: Final[str] = "ioa_observe.workflow.name"
AGENT_CHAIN_START_TIME: Final[str] = "agent_chain_start_time"
SPAN_NAME_AGENT_START_EVENT: Final[str] = "agent_start_event"
SPAN_NAME_AGENT_END_EVENT: Final[str] = "agent_end_event"
EXECUTION_SUCCESS: Final[str] = "execution.success"
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
OBSERVE_MEMORY_OPERATION: Final[str] = "ioa_observe.memory.operation"
OBSERVE_MEMORY_TYPE: Final[str] = "ioa_observe.memory.type"
OBSERVE_PROCESSING_TYPE: Final[str] = "ioa_observe.processing.type"
OBSERVE_PROCESSING_ACTOR: Final[str] = "ioa_observe.processing.actor"
OBSERVE_GOVERNANCE_DECISION_TYPE: Final[str] = "ioa_observe.governance.decision_type"
OBSERVE_GOVERNANCE_POLICY_ID: Final[str] = "ioa_observe.governance.policy_id"
SPAN_SUFFIX_AGENT: Final[str] = "agent"
SPAN_SUFFIX_CHAT: Final[str] = "chat"
SPAN_SUFFIX_TOOL: Final[str] = "tool"
SPAN_SUFFIX_GRAPH: Final[str] = "graph"
SPAN_SUFFIX_MEMORY: Final[str] = "memory"
SPAN_SUFFIX_PROCESSING: Final[str] = "processing"
SPAN_SUFFIX_CONTEXT: Final[str] = "context"
SPAN_SUFFIX_GOVERNANCE: Final[str] = "governance"
SPAN_SUFFIX_SKILL: Final[str] = "skill"
SPAN_SUFFIX_RAG: Final[str] = "rag"

# TaskCall is the MAS-run wrapper. Observe-sdk names the equivalent span
# ``invoke_agent {graph}`` (e.g. ``invoke_agent LangGraph``). SCOPE ``norm``
# ignores it; Inspect still shows it as the workflow root.
SPAN_NAME_TASK_CALL: Final[str] = "invoke_agent mas"
SPAN_NAME_SESSION_START: Final[str] = "session.start"
SPAN_NAME_SESSION_END: Final[str] = "session.end"


def invoke_agent_span_name(app_name: str | None) -> str:
    """Observe-sdk workflow root: ``invoke_agent LangGraph`` → ``invoke_agent {app}``."""
    return f"invoke_agent {str(app_name or 'mas').strip() or 'mas'}"

try:
    from ioa_observe.sdk.utils.const import (  # type: ignore[import-not-found]
        OBSERVE_SPAN_SUFFIX_AGENT as SPAN_SUFFIX_AGENT,
        OBSERVE_SPAN_SUFFIX_CHAT as SPAN_SUFFIX_CHAT,
        OBSERVE_SPAN_SUFFIX_GRAPH as SPAN_SUFFIX_GRAPH,
        OBSERVE_SPAN_SUFFIX_TOOL as SPAN_SUFFIX_TOOL,
    )
except ImportError:
    pass
try:
    from ioa_observe.sdk.utils.const import (  # type: ignore[import-not-found]
        OBSERVE_SPAN_SUFFIX_CONTEXT as SPAN_SUFFIX_CONTEXT,
        OBSERVE_SPAN_SUFFIX_GOVERNANCE as SPAN_SUFFIX_GOVERNANCE,
        OBSERVE_SPAN_SUFFIX_MEMORY as SPAN_SUFFIX_MEMORY,
        OBSERVE_SPAN_SUFFIX_PROCESSING as SPAN_SUFFIX_PROCESSING,
        OBSERVE_SPAN_SUFFIX_RAG as SPAN_SUFFIX_RAG,
        OBSERVE_SPAN_SUFFIX_SKILL as SPAN_SUFFIX_SKILL,
    )
except ImportError:
    pass


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
