#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock infrastructure adapters for simulated runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mas.runtime.boundary.context.conversation_chunks import ConversationChunkStore
from mas.runtime.boundary.context.dp_inject import inject_dp_protocol
from mas.runtime.boundary.context.plugin_collection import PluginCollection
from mas.runtime.boundary.context.working_memory import WorkingMemoryStore
from mas.runtime.schema.egress import RequestCtxAssembly
from mas.runtime.schema.ingress import CtxAssemblyComplete

if TYPE_CHECKING:
    from mas.runtime.kernel.state import QProduct


@dataclass
class AutoCtxAssembler:
    """Immediately complete context assembly on REQUEST_CTX_ASSEMBLY."""

    injected_context: list[str] = field(default_factory=list)
    memory_seeds: list[tuple[str, str]] = field(default_factory=list)
    last_user_text: str = ""
    turn_index: int = 0
    turn_history: list[tuple[str, str]] = field(default_factory=list)
    committed_messages: list[dict[str, Any]] = field(default_factory=list)
    conversation_chunks: ConversationChunkStore = field(default_factory=ConversationChunkStore)
    working_memory: WorkingMemoryStore = field(default_factory=WorkingMemoryStore)
    archive_dir: str = ""
    archive_watermark_bytes: int | None = None
    checkpoint_portability: str = "self_contained"
    manifest: dict[str, Any] | None = None
    pattern_plugin_id: str = "react@v1"
    runtime_params: dict[str, Any] = field(default_factory=dict)
    # Per-run dataset fixtures for mock tools; read via contracts.tool_fixture().
    tool_fixtures: Any = None
    q_product: QProduct | None = None
    observability: Any | None = None
    last_context_usage: dict[str, Any] | None = None
    context_usage_parts: list[dict[str, Any]] = field(default_factory=list)
    _assembly_correlation_id: int = 0
    # PluginCollection holding ContextContract plugins (e.g. SkillCatalogPlugin).
    # collect_results("collect_context") is called by assemble_llm_messages() on
    # every pre-LLM assembly pass (v0.1 wiring for ctx_collect_execute FSM symbol).
    # Also serves as agent.registry for ContextAssemblerPlugin.attach_agent().
    plugin_collection: PluginCollection = field(default_factory=PluginCollection)
    # SkillRegistry populated by SkillCatalogPlugin; read by SkillToolsPlugin.
    skill_registry: Any | None = None
    # SkillSessionState for activation deduplication (agentskills.io Step 5).
    skill_session_state: Any | None = None
    # ActivatedSkillsContextPlugin for compaction protection (agentskills.io Step 5).
    activated_skills_plugin: Any | None = None
    skill_pin_activated: bool = True
    skill_allow_unload: bool = False
    # Runtime context for system tools (session_id, agent_id, correlation_id)
    session_id: str = ""
    agent_id: str = ""
    correlation_id: int = 0
    allow_subagent_spawning: bool = False
    subagent_templates: list[dict[str, Any]] = field(default_factory=list)

    def capture_baseline(self) -> None:
        """Snapshot manifest-derived system context for /reset."""
        self._baseline_injected_context = list(self.injected_context)

    def reset_conversation(self) -> None:
        """Clear turn history and working memory; restore baseline system prompt."""
        baseline = getattr(self, "_baseline_injected_context", None)
        if baseline is not None:
            self.injected_context = list(baseline)
        self.turn_history.clear()
        self.committed_messages.clear()
        self.conversation_chunks = ConversationChunkStore()
        self.working_memory.clear()
        self.last_user_text = ""
        self.turn_index = 0
        self.runtime_params = {}
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        record_context_mutation(
            self.observability,
            action="session_reset",
            committed_count=0,
            wm_count=0,
        )

    def note_user_input(self, text: str) -> None:
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        record_context_mutation(
            self.observability,
            action="turn_start",
            turn_index=self.turn_index + 1,
            wm_count=len(self.working_memory.messages),
            committed_count=len(self.committed_messages),
        )
        self.last_user_text = text
        self.turn_index += 1
        self.working_memory.clear()
        record_context_mutation(
            self.observability,
            action="wm_clear",
            turn_index=self.turn_index,
            committed_count=len(self.committed_messages),
        )

    def note_agent_response(self, text: str) -> None:
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        committed_before = len(self.committed_messages)
        if self.last_user_text:
            self.committed_messages.append({"role": "user", "content": self.last_user_text})
            self.turn_history.append((self.last_user_text, text))
            # A turn can now be finalized more than once (HITL pause, then
            # resume — see SessionController._finalize_turn): once the user
            # turn is committed, clear it so a second finalize of the SAME
            # turn doesn't re-append it. Safe within a turn's own multi-step
            # tool loop too, since note_agent_response only ever runs at
            # turn end/pause, never between a turn's own internal LLM calls.
            self.last_user_text = ""
        if self.working_memory.messages:
            self.committed_messages.extend(self.working_memory.messages)
        if text.strip():
            last = self.committed_messages[-1] if self.committed_messages else {}
            if not (
                last.get("role") == "assistant"
                and str(last.get("content") or "").strip() == text.strip()
            ):
                self.committed_messages.append({"role": "assistant", "content": text})
        turn_messages = list(self.committed_messages[committed_before:])
        if turn_messages:
            self.conversation_chunks.append_turn(turn_messages)
            from mas.runtime.boundary.context.assemble import compact_committed_history

            compact_committed_history(self)
        record_context_mutation(
            self.observability,
            action="turn_commit",
            turn_index=self.turn_index,
            content=text,
            committed_count=len(self.committed_messages),
            wm_count=len(self.working_memory.messages),
        )
        self.working_memory.clear()

    def record_assistant_tool_call(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        self.working_memory.record_assistant_tool_call(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        record_context_mutation(
            self.observability,
            action="wm_append",
            turn_index=self.turn_index,
            role="assistant",
            call_id=call_id,
            content=f"tool_call:{tool_name}",
            wm_count=len(self.working_memory.messages),
            committed_count=len(self.committed_messages),
        )

    def record_assistant_tool_calls(
        self,
        calls: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        self.working_memory.record_assistant_tool_calls(calls)

    def record_tool_result(
        self,
        *,
        call_id: str,
        content: str,
        chunk: dict[str, Any] | None = None,
    ) -> None:
        from mas.runtime.boundary.context.telemetry import record_context_mutation

        self.working_memory.record_tool_result(call_id=call_id, content=content, chunk=chunk)
        record_context_mutation(
            self.observability,
            action="wm_append",
            turn_index=self.turn_index,
            role="tool",
            call_id=call_id,
            content=content,
            wm_count=len(self.working_memory.messages),
            committed_count=len(self.committed_messages),
        )

    def record_assistant_message(self, content: str) -> None:
        self.working_memory.record_assistant_message(content)

    def complete(self, request: RequestCtxAssembly) -> CtxAssemblyComplete:
        if request.operator_context:
            self.injected_context.append(request.operator_context)
        for key, content in self.memory_seeds:
            self.injected_context.append(f"[memory:{key}] {content}")
        self.injected_context = inject_dp_protocol(
            self.injected_context,
            pattern_plugin_id=self.pattern_plugin_id,
            q=self.q_product,
        )
        return CtxAssemblyComplete(collect_id=request.collect_id)
