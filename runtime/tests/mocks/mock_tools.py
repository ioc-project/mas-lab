#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock tool contracts for testing."""

from typing import Any, Dict, List, Optional


class MockToolContract:
    """Mock tool that returns predefined results."""

    def __init__(
        self,
        name: str = "mock_tool",
        description: str = "A mock tool for testing",
        results: Optional[List[Any]] = None,
        should_fail: bool = False,
        **kwargs,
    ):
        """Initialize mock tool.

        Args:
            name: Tool name
            description: Tool description
            results: List of results to return in sequence
            should_fail: Whether tool should raise exceptions
            **kwargs: Additional config
        """
        super().__init__()
        self.name = name
        self.description = description
        self.results = results or ["Mock result"]
        self.should_fail = should_fail
        self.call_count = 0
        self.call_history: List[Dict[str, Any]] = []

    def execute(self, **kwargs) -> Any:
        """Execute mock tool.

        Args:
            **kwargs: Tool parameters

        Returns:
            Next result from predefined list

        Raises:
            RuntimeError: If should_fail is True
        """
        # Record call
        self.call_history.append(kwargs)

        if self.should_fail:
            self.call_count += 1
            raise RuntimeError(f"Mock tool {self.name} failed (call {self.call_count})")

        # Return next result
        result = self.results[self.call_count % len(self.results)]
        self.call_count += 1
        return result

    def get_schema(self) -> Dict[str, Any]:
        """Get tool schema.

        Returns:
            Tool schema dict
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }

    def reset(self):
        """Reset call counter and history."""
        self.call_count = 0
        self.call_history.clear()


class MockToolProvider:
    """Mock tool provider that manages multiple tools."""

    def __init__(self, tools: Optional[List[MockToolContract]] = None):
        """Initialize with tools.

        Args:
            tools: List of mock tools
        """
        self.tools = {tool.name: tool for tool in (tools or [])}

    def get_tool(self, name: str) -> Optional[MockToolContract]:
        """Get tool by name.

        Args:
            name: Tool name

        Returns:
            Tool contract or None
        """
        return self.tools.get(name)

    def list_tools(self) -> List[str]:
        """List available tool names.

        Returns:
            List of tool names
        """
        return list(self.tools.keys())

    def add_tool(self, tool: MockToolContract):
        """Add a tool.

        Args:
            tool: Tool to add
        """
        self.tools[tool.name] = tool

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Get schemas for all tools.

        Returns:
            List of tool schemas
        """
        return [tool.get_schema() for tool in self.tools.values()]
