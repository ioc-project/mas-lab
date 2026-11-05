#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ContextResolver — deterministic tool/memory access utility.

Covers:
- ContextResolver tool dispatch (call_tool)
- ContextResolver memory queries (query_memory)
- ContextResolver agent config/id access
- Usage pattern inside ContextContract.collect_context()
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from mas.runtime.contracts.context_contract import (
    ContextContract,
    ContextPart,
    ContextPlacement,
    ContextResolver,
)

# =====================================================================
# Test helpers
# =====================================================================


class _FakeToolPlugin:
    """Minimal ToolContract-like object for testing."""

    def __init__(self, tool_name: str, result: Any):
        self._name = tool_name
        self._result = result

    def list_tools(self) -> List[Dict[str, Any]]:
        return [{"name": self._name}]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == self._name:
            return self._result
        raise ValueError(f"Unknown tool: {tool_name}")


class _FakeMemoryPlugin:
    """Minimal MemoryContract-like object for testing."""

    def __init__(self, items: List[Dict[str, Any]]):
        self._items = items

    def read_memory(self, memory_type: str, **kwargs) -> Dict[str, Any]:
        return {"status": "ok", "items": self._items}


def _make_registry(
    tool_plugins: list | None = None,
    memory_plugins: list | None = None,
) -> MagicMock:
    registry = MagicMock()

    def get_plugins_by_type(plugin_type):
        from mas.runtime.contracts.memory_contract import MemoryContract
        from mas.runtime.contracts.tool_contract import ToolContract

        if plugin_type is ToolContract:
            return tool_plugins or []
        if plugin_type is MemoryContract:
            return memory_plugins or []
        return []

    registry.get_plugins_by_type = get_plugins_by_type
    return registry


# =====================================================================
# ContextResolver — tool dispatch
# =====================================================================


class TestContextResolverTools:
    def test_call_tool_success(self):
        tool = _FakeToolPlugin("calc", {"result": 42})
        resolver = ContextResolver(registry=_make_registry(tool_plugins=[tool]))
        assert resolver.call_tool("calc", {"expr": "6*7"}) == {"result": 42}

    def test_call_tool_not_found(self):
        resolver = ContextResolver(registry=_make_registry(tool_plugins=[]))
        with pytest.raises(LookupError, match="No plugin provides tool 'missing'"):
            resolver.call_tool("missing", {})

    def test_call_tool_selects_correct_plugin(self):
        t1 = _FakeToolPlugin("alpha", "a")
        t2 = _FakeToolPlugin("beta", "b")
        resolver = ContextResolver(registry=_make_registry(tool_plugins=[t1, t2]))
        assert resolver.call_tool("beta", {}) == "b"


# =====================================================================
# ContextResolver — memory queries
# =====================================================================


class TestContextResolverMemory:
    def test_query_memory(self):
        mem = _FakeMemoryPlugin([{"content": "fact-1"}, {"content": "fact-2"}])
        resolver = ContextResolver(registry=_make_registry(memory_plugins=[mem]))
        items = resolver.query_memory("what is X?")
        assert len(items) == 2
        assert items[0]["content"] == "fact-1"

    def test_query_memory_respects_limit(self):
        mem = _FakeMemoryPlugin([{"content": f"f{i}"} for i in range(10)])
        resolver = ContextResolver(registry=_make_registry(memory_plugins=[mem]))
        items = resolver.query_memory("q", limit=3)
        assert len(items) == 3

    def test_query_memory_empty(self):
        resolver = ContextResolver(registry=_make_registry(memory_plugins=[]))
        assert resolver.query_memory("q") == []


# =====================================================================
# ContextContract + ContextResolver usage pattern
# =====================================================================


class _MetricsPlugin(ContextContract):
    """Example: computed context using ContextResolver inside collect_context."""

    def collect_context(self, request=None) -> List[ContextPart]:
        if self.resolver is None:
            return []
        raw = self.resolver.call_tool("fetch_metrics", {"window": "5m"})
        return [
            ContextPart.system(
                content=f"Metrics: {raw}",
                source="metrics",
                placement=ContextPlacement.SYSTEM_BODY,
            )
        ]


class TestResolverInPlugin:
    def test_plugin_with_resolver(self):
        """Plugin uses ContextResolver explicitly inside collect_context."""
        tool = _FakeToolPlugin("fetch_metrics", {"cpu": 42})
        registry = _make_registry(tool_plugins=[tool])
        agent = MagicMock()
        agent.registry = registry

        plugin = _MetricsPlugin()
        plugin.agent = agent
        parts = plugin.collect_context()
        assert len(parts) == 1
        assert "cpu" in parts[0].content

    def test_plugin_without_agent_returns_empty(self):
        """Without agent, plugin gracefully returns empty."""
        plugin = _MetricsPlugin()
        parts = plugin.collect_context()
        assert parts == []


# =====================================================================
# ContextContract — basic behavior unchanged
# =====================================================================


class _SimplePlugin(ContextContract):
    def collect_context(self, request=None) -> List[ContextPart]:
        return [ContextPart.system(content="hello", source="test")]


class TestContextContractBasic:
    def test_collect_context(self):
        p = _SimplePlugin()
        parts = p.collect_context()
        assert len(parts) == 1
        assert parts[0].content == "hello"

    def test_default_returns_empty(self):
        p = ContextContract()
        assert p.collect_context() == []

    def test_no_speculative_methods(self):
        """ContextContract should NOT have describe/is_stale/resolve_context."""
        p = ContextContract()
        assert not hasattr(p, "describe")
        assert not hasattr(p, "is_stale")
        assert not hasattr(p, "resolve_context")
        assert not hasattr(p, "_make_provenance")
        assert not hasattr(p, "source_type")

    def test_resolver_property_no_agent(self):
        """resolver returns None without an agent."""
        p = ContextContract()
        assert p.resolver is None

    def test_resolver_property_with_agent(self):
        """resolver returns a ContextResolver when agent+registry are available."""
        agent = MagicMock()
        agent.registry = MagicMock()
        agent.recorder = MagicMock()
        p = ContextContract()
        p.agent = agent
        r = p.resolver
        assert isinstance(r, ContextResolver)
        # Cached on second access
        assert p.resolver is r


# =====================================================================
# ContextResolver — recorder emission
# =====================================================================


class TestContextResolverRecorder:
    def test_call_tool_emits_event(self):
        """call_tool emits a deterministic_tool_call recorder event."""
        tool = _FakeToolPlugin("calc", 42)
        recorder = MagicMock()
        agent = MagicMock()
        agent.recorder = recorder
        agent.agent_id = "test-agent"
        resolver = ContextResolver(registry=_make_registry(tool_plugins=[tool]), agent=agent)
        resolver.call_tool("calc", {"x": 1})
        recorder.emit.assert_called_once()
        event = recorder.emit.call_args[0][0]
        assert event["kind"] == "deterministic_tool_call"
        assert event["tool_name"] == "calc"
        assert event["access_mechanism"] == "rag"
        assert event["agent_id"] == "test-agent"

    def test_query_memory_emits_event(self):
        """query_memory emits a deterministic_memory_query recorder event."""
        mem = _FakeMemoryPlugin([{"content": "fact"}])
        recorder = MagicMock()
        agent = MagicMock()
        agent.recorder = recorder
        agent.agent_id = "test-agent"
        resolver = ContextResolver(
            registry=_make_registry(memory_plugins=[mem]),
            agent=agent,
        )
        resolver.query_memory("q")
        recorder.emit.assert_called_once()
        event = recorder.emit.call_args[0][0]
        assert event["kind"] == "deterministic_memory_query"
        assert event["items_returned"] == 1
        assert event["access_mechanism"] == "rag"

    def test_no_recorder_no_error(self):
        """Without a recorder, call_tool/query_memory still work."""
        tool = _FakeToolPlugin("calc", 42)
        resolver = ContextResolver(registry=_make_registry(tool_plugins=[tool]))
        assert resolver.call_tool("calc", {}) == 42
