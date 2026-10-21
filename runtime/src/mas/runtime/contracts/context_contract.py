#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Context Contract — modular, plugin-driven prompt composition.

Motivation
----------
The prompt that reaches the LLM is not a single monolithic string.
It is a *composition* of typed sections contributed by many concerns:

    system identity / role
    design-pattern instructions (CoT chain, ReAct format)
    available external tools    (from ToolContract)
    available sub-agents        (from DelegationContract / engine_tool_provider)
    active skills summary       (from skill context plugins)
    relevant memories           (from MemoryContract)
    conversation history        (maintained by the runtime)
    current task / user turn

Without a dedicated assembly layer, each concern independently mutates
``messages[]`` inside ``on_pre_llm_call``, producing fragile ordering
dependencies and making context-budget management impossible.

The ``ContextContract`` solves this by separating two responsibilities:

    1. *Contribution* — each plugin emits ``ContextPart`` objects via
       ``on_collect_context()``.
    2. *Assembly* — a single ``ContextAssemblerPlugin`` reads all parts,
       orders them by ``placement + priority``, trims to the token budget,
       and writes the final ``messages[]`` list once.

Design alignment with agent-remote
--------------------------
agent-remote's ``Task.message.parts`` is a ``List[TextPart | DataPart | FilePart]``.
Our ``ContextPart`` maps directly: a context section that crosses an agent
boundary becomes an agent-remote ``Part`` without loss of typing.

Hook name
---------
``collect_context`` (new, aggregating) — each plugin's
``on_collect_context()`` returns ``List[ContextPart]``.  The registry
collects all lists with ``collect_results("collect_context")``.

Placement ordering (rendered top → bottom inside the system message)
-------------------------------------------------------------------
    system_header   — identity, role, date context           (priority 0-9)
    system_pattern  — design-pattern / reasoning style      (priority 10-19)
    system_agents   — available sub-agent directory         (priority 20-29)
    system_tools    — available external tool directory     (priority 30-39)
    system_skills   — skills summary                        (priority 40-49)
    system_ontology — ontology / shared vocabulary hints    (priority 50-59)
    system_body     — any other system content              (priority 60-99)
    system_memory   — injected memories / context recall    (priority 100+)
    user_prepend    — prepended before the user turn        (ordering same)
    user_append     — appended after the user turn          (ordering same)
    assistant       — injected assistant-role messages (rare)
"""

from __future__ import annotations

import logging
import time as _time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from mas.runtime.contracts.base import BasePlugin  # L3->L3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compat re-exports (moved to memory_contract.py)
# ---------------------------------------------------------------------------
from mas.runtime.contracts.memory_contract import (  # noqa: F401
    RetentionPolicy,
    WorkingMemoryAnnotation,
)


@dataclass
class ContextProvenance:
    """Structured provenance for a context contribution."""

    semantic_role: str = ""
    mechanism: str = "collect_context"
    trigger: str = "runtime_default"
    actor: str = "runtime"
    source_type: str = "unknown"
    source_id: str = ""
    operation: str = "context_injection"
    via: str = "collect_context"
    sensitivity: str = ""
    annotations: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(
        cls,
        value: Optional["ContextProvenance | Dict[str, Any]"],
        *,
        role: str,
        source: str,
        section_id: str,
    ) -> "ContextProvenance":
        if isinstance(value, cls):
            provenance = value
        else:
            provenance = cls(**(value or {}))

        if not provenance.semantic_role:
            provenance.semantic_role = role or source
        if not provenance.source_type:
            provenance.source_type = source.split(":", 1)[0] if source else "unknown"
        if not provenance.source_id:
            provenance.source_id = section_id
        return provenance

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "semantic_role": self.semantic_role,
            "mechanism": self.mechanism,
            "trigger": self.trigger,
            "actor": self.actor,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "operation": self.operation,
            "via": self.via,
        }
        if self.annotations:
            payload["annotations"] = dict(self.annotations)
        if self.sensitivity:
            payload["sensitivity"] = self.sensitivity
        return payload


# ---------------------------------------------------------------------------
# Context resolver — deterministic tool/memory access for plugins
# ---------------------------------------------------------------------------


class ContextResolver:
    """Utility for deterministic (non-LLM) tool and memory access.

    ``ContextContract`` subclasses should use the ``resolver`` property
    instead of instantiating this directly::

        class MetricsPlugin(ContextContract):
            def collect_context(self) -> List[ContextPart]:
                if self.resolver is None:
                    return []
                data = self.resolver.call_tool("fetch_metrics", {"window": "5m"})
                return [ContextPart.system(content=str(data), source="metrics")]

    This enables the "content manager" pattern: a context source calls
    a tool, processes the result, and injects it into the prompt —
    without going through the LLM's tool-use loop.  Every call emits
    a recorder event so deterministic access has the same observability
    as LLM-driven tool calls.

    Parameters
    ----------
    registry:
        The agent's :class:`PluginRegistry`.
    agent:
        The agent instance (optional; used for config and recorder access).
    """

    def __init__(self, registry: Any, agent: Any = None) -> None:
        self._registry = registry
        self._agent = agent
        self._recorder = getattr(agent, "recorder", None)

    # -- Tool execution ---------------------------------------------------

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a registered tool deterministically.

        Iterates over ``ToolContract`` plugins to find the owner of
        *tool_name* and invokes ``call_tool`` on it.  This bypasses LLM
        decision-making but uses the same execution contract.

        Emits a ``deterministic_tool_call`` recorder event for
        observability parity with LLM-driven tool calls.

        Raises
        ------
        LookupError
            If no registered plugin provides *tool_name*.
        """
        from mas.runtime.contracts.tool_contract import ToolContract

        for plugin in self._registry.get_plugins_by_type(ToolContract):
            tools = plugin.list_tools()
            if any(t.get("name") == tool_name for t in tools):
                result = plugin.call_tool(tool_name, arguments)
                if self._recorder is not None:
                    self._recorder.emit({
                        "kind": "deterministic_tool_call",
                        "agent_id": self._agent_id,
                        "timestamp": _time.time(),
                        "tool_name": tool_name,
                        "mechanism": "context_resolver",
                        "access_mechanism": "rag",
                        "source_type": "tool",
                    })
                return result
        raise LookupError(f"No plugin provides tool '{tool_name}'")

    # -- Memory queries ---------------------------------------------------

    def query_memory(
        self,
        query: str,
        memory_type: str = "semantic",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Query memory stores via registered ``MemoryContract`` plugins.

        Emits a ``deterministic_memory_query`` recorder event for
        observability.

        Parameters
        ----------
        query:
            Search query or key.
        memory_type:
            Memory type to query (``"episodic"``, ``"semantic"``,
            ``"procedural"``).
        limit:
            Maximum number of results.

        Returns
        -------
        List of memory items (dicts with at least ``"content"`` key).
        """
        from mas.runtime.contracts.memory_contract import MemoryContract

        results: List[Dict[str, Any]] = []
        for plugin in self._registry.get_plugins_by_type(MemoryContract):
            try:
                resp = plugin.read_memory(
                    memory_type=memory_type,
                    query={"search": query, "limit": limit},
                )
                items = resp.get("items", []) if isinstance(resp, dict) else []
                results.extend(items)
            except Exception as exc:
                logger.debug(
                    "ContextResolver.query_memory: %s raised %s",
                    plugin.__class__.__name__,
                    exc,
                )
        results = results[:limit]
        if self._recorder is not None:
            self._recorder.emit({
                "kind": "deterministic_memory_query",
                "agent_id": self._agent_id,
                "timestamp": _time.time(),
                "memory_type": memory_type,
                "items_returned": len(results),
                "mechanism": "context_resolver",
                "access_mechanism": "rag",
                "source_type": "memory",
            })
        return results

    # -- Agent config access ----------------------------------------------

    @property
    def _agent_id(self) -> str:
        if self._agent is not None:
            return getattr(self._agent, "agent_id", "unknown") or "unknown"
        return "unknown"


# ---------------------------------------------------------------------------
# Placement taxonomy
# ---------------------------------------------------------------------------


class ContextPlacement(str, Enum):
    """Where in the final messages list this part will be rendered."""

    SYSTEM_HEADER = "system_header"
    SYSTEM_PATTERN = "system_pattern"
    SYSTEM_AGENTS = "system_agents"
    SYSTEM_TOOLS = "system_tools"
    SYSTEM_SKILLS = "system_skills"
    SYSTEM_ONTOLOGY = "system_ontology"
    SYSTEM_BODY = "system_body"
    SYSTEM_MEMORY = "system_memory"
    USER_PREPEND = "user_prepend"
    USER_APPEND = "user_append"
    ASSISTANT = "assistant"


# Render order: placements that end up in the system message,
# sorted so the assembled system message reads top-to-bottom.
_SYSTEM_PLACEMENTS_ORDER: List[ContextPlacement] = [
    ContextPlacement.SYSTEM_HEADER,
    ContextPlacement.SYSTEM_PATTERN,
    ContextPlacement.SYSTEM_AGENTS,
    ContextPlacement.SYSTEM_TOOLS,
    ContextPlacement.SYSTEM_SKILLS,
    ContextPlacement.SYSTEM_ONTOLOGY,
    ContextPlacement.SYSTEM_BODY,
    ContextPlacement.SYSTEM_MEMORY,
]


# ---------------------------------------------------------------------------
# ContextPart — the unit contributed by each plugin
# ---------------------------------------------------------------------------


@dataclass
class ContextPart:
    """A single typed section contributed to the prompt by a plugin.

    Attributes
    ----------
    content:
        Rendered text for this section (Markdown OK).
    placement:
        Where in the assembled messages list this section belongs.
    priority:
        Within a placement, lower value = rendered earlier.
        Each placement band has a suggested range (see module docstring).
    source:
        Logical name of the contributing plugin / concern,
        e.g. ``"tools"``, ``"agents"``, ``"memory"``, ``"skills"``.
    section_id:
        Stable identifier used by context-management strategies to evict
        or swap sections (e.g. ``"memory/episodic"``, ``"agents/telemetry"``).
    token_estimate:
        Rough token count.  If None the assembler estimates from content
        length (chars / 4).  Used by budget-aware strategies.
    pinned:
        If True, context-management strategies MUST NOT evict this part.
    expires_after_turns:
        If set, the assembler should drop this part after N conversation
        turns (managed by a strategy plugin, not by the base assembler).
    metadata:
        Arbitrary extra data passed through for strategy use.
    """

    content: str
    part_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    placement: ContextPlacement = ContextPlacement.SYSTEM_BODY
    priority: int = 60
    source: str = "unknown"
    section_id: str = ""
    role: str = ""  # semantic provenance: e.g. "tool_result", "assertion", "instruction",
                    # "shared_context", "specialist_output", "ontology", "memory"
                    # distinct from placement (rendering position) and source (plugin name).
                    # Falls back to source when empty.
    token_estimate: Optional[int] = None
    pinned: bool = False
    restricted: bool = False  # If True, this part MUST NOT cross agent boundaries.
                              # OC metric A6 — Context Boundary Violation Count.
    expires_after_turns: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Optional[ContextProvenance | Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.section_id:
            self.section_id = f"{self.source}/{self.placement.value}"
        if not self.role:
            self.role = self.source
        if self.token_estimate is None:
            self.token_estimate = max(1, len(self.content) // 4)
        self.provenance = ContextProvenance.from_value(
            self.provenance,
            role=self.role or self.source,
            source=self.source,
            section_id=self.section_id,
        )

    def to_observability_dict(self) -> Dict[str, Any]:
        payload = {
            "part_id": self.part_id,
            "role": self.role or self.source,
            "source": self.source,
            "section_id": self.section_id,
            "placement": self.placement.value,
            "priority": self.priority,
            "tokens": self.token_estimate,
            "pinned": self.pinned,
            "restricted": self.restricted,
            "content": self.content,
            "provenance": self.provenance.to_dict() if isinstance(self.provenance, ContextProvenance) else {},
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        if self.expires_after_turns is not None:
            payload["expires_after_turns"] = self.expires_after_turns
        return payload

    # Convenience constructors -------------------------------------------

    @classmethod
    def system(
        cls,
        content: str,
        source: str,
        placement: ContextPlacement = ContextPlacement.SYSTEM_BODY,
        priority: int = 60,
        **kwargs: Any,
    ) -> "ContextPart":
        """Shorthand for a system-role section."""
        return cls(
            content=content,
            placement=placement,
            priority=priority,
            source=source,
            **kwargs,
        )

    @classmethod
    def agents(cls, content: str, source: str = "agents", **kwargs: Any) -> "ContextPart":
        """Shorthand: agent-directory section."""
        kwargs.setdefault("role", "shared_context")
        kwargs.setdefault(
            "provenance",
            {
                "mechanism": "runtime_injection",
                "trigger": "runtime_default",
                "actor": "runtime",
                "source_type": "agents",
            },
        )
        return cls(
            content=content,
            placement=ContextPlacement.SYSTEM_AGENTS,
            priority=20,
            source=source,
            **kwargs,
        )

    @classmethod
    def tools(cls, content: str, source: str = "tools", **kwargs: Any) -> "ContextPart":
        """Shorthand: tool-directory section."""
        kwargs.setdefault("role", "instruction")
        kwargs.setdefault(
            "provenance",
            {
                "mechanism": "runtime_injection",
                "trigger": "runtime_default",
                "actor": "runtime",
                "source_type": "tools",
            },
        )
        return cls(
            content=content,
            placement=ContextPlacement.SYSTEM_TOOLS,
            priority=30,
            source=source,
            **kwargs,
        )

    @classmethod
    def memory(cls, content: str, source: str = "memory", **kwargs: Any) -> "ContextPart":
        """Shorthand: memory recall section (evictable by default)."""
        kwargs.setdefault("role", "memory")
        kwargs.setdefault(
            "provenance",
            {
                "mechanism": "memory_read",
                "trigger": "runtime_default",
                "actor": "runtime",
                "source_type": "memory",
            },
        )
        return cls(
            content=content,
            placement=ContextPlacement.SYSTEM_MEMORY,
            priority=100,
            source=source,
            **kwargs,
        )

    @classmethod
    def skills(cls, content: str, source: str = "skills", **kwargs: Any) -> "ContextPart":
        """Shorthand: skill catalog section (pinned by default).

        Placed at ``SYSTEM_SKILLS`` (priority 40), between tool directory and
        ontology.  Pinned so context-budget strategies do not evict the
        catalog — the model needs it throughout the session to know which
        skills are available.
        """
        kwargs.setdefault("role", "instruction")
        kwargs.setdefault("pinned", True)
        kwargs.setdefault(
            "provenance",
            {
                "mechanism": "inject",
                "trigger": "session_start",
                "actor": "runtime",
                "source_type": "skill",
                "operation": "skill_catalog_injection",
                "via": "collect_context",
            },
        )
        priority = kwargs.pop("priority", 40)
        return cls(
            content=content,
            placement=ContextPlacement.SYSTEM_SKILLS,
            priority=priority,
            source=source,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# ContextContract — base class for contributing plugins
# ---------------------------------------------------------------------------


class ContextContract(BasePlugin):
    """Base class for plugins that contribute context sections to the prompt.

    Any plugin that wants to inject content into the assembled prompt should
    inherit ``ContextContract`` and implement ``collect_context()``.

    The ``on_collect_context()`` hook method is called by the PluginRegistry
    via ``collect_results("collect_context")``.  Plugins return a list of
    ``ContextPart`` objects — they do NOT directly modify ``messages[]``.

    This decouples contribution from assembly, enabling:
    - Context budget management (assembler decides what fits)
    - Stable ordering independent of plugin registration order
    - Testability (assert on parts, not on raw strings)
    - Eviction strategies (memory compression, sliding window)
    - agent-remote interoperability (parts map to agent-remote message parts)

    For plugins that need deterministic tool or memory access during context
    collection, use the ``resolver`` property::

        class MetricsProvider(ContextContract):
            def collect_context(self) -> List[ContextPart]:
                if self.resolver is None:
                    return []
                raw = self.resolver.call_tool("fetch_metrics", {"window": "5m"})
                return [ContextPart.system(content=str(raw), source="metrics")]

    The resolver routes through contracts and emits recorder events so
    every deterministic access is observable (same as LLM-driven calls).

    Example — simple source
    -----------------------
    ::

        class MyOntologyContextProvider(ContextContract):
            def collect_context(self) -> List[ContextPart]:
                snippet = self._load_ontology_snippet()
                return [ContextPart.system(
                    content=snippet,
                    source="ontology",
                    placement=ContextPlacement.SYSTEM_ONTOLOGY,
                    section_id="ontology/opentel",
                    pinned=True,
                )]
    """

    def collect_context(self) -> List[ContextPart]:
        """Return the list of context parts this plugin contributes.

        Returns an empty list by default (opt-in).
        """
        return []

    @property
    def resolver(self) -> Optional["ContextResolver"]:
        """Lazy-built :class:`ContextResolver` for deterministic tool/memory
        access.

        Returns ``None`` when no agent or registry is available (e.g. in
        unit tests).  The resolver is cached after first access.
        """
        cached = getattr(self, "_resolver", None)
        if cached is not None:
            return cached
        agent = getattr(self, "agent", None)
        registry = getattr(agent, "registry", None) if agent else None
        if registry is None:
            return None
        resolver = ContextResolver(registry, agent)
        self._resolver = resolver
        return resolver

    def on_collect_context(self) -> List[ContextPart]:
        """Hook handler: delegate to ``collect_context()``."""
        return self.collect_context()
