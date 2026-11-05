#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock Context Manager contract for testing."""

from typing import Any, Dict, List


class MockContextManagerContract:
    """Mock context manager that simulates conversation management."""

    def __init__(self, name: str = "mock_cm", max_messages: int = 10, trim_strategy: str = "fifo", **kwargs):
        """Initialize mock CM.

        Args:
            name: CM name
            max_messages: Maximum messages to keep
            trim_strategy: How to trim messages ("fifo", "lifo", "none")
            **kwargs: Additional config
        """
        super().__init__()
        self.name = name
        self.max_messages = max_messages
        self.trim_strategy = trim_strategy
        self.trim_count = 0
        self.assemble_count = 0

    def assemble(self, messages: List[Dict[str, str]], **kwargs) -> List[Dict[str, str]]:
        """Assemble conversation context.

        Args:
            messages: Full message history
            **kwargs: Additional parameters

        Returns:
            Trimmed/processed messages
        """
        self.assemble_count += 1

        # No trimming needed
        if len(messages) <= self.max_messages:
            return messages

        # Trim based on strategy
        self.trim_count += 1

        if self.trim_strategy == "none":
            return messages
        elif self.trim_strategy == "lifo":
            # Keep most recent messages
            return messages[-self.max_messages :]
        else:  # fifo (default)
            # Keep oldest messages (typically system + recent)
            # Keep first message (system) + recent messages
            if len(messages) <= 1:
                return messages
            return [messages[0]] + messages[-(self.max_messages - 1) :]

    def estimate_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Estimate token count.

        Args:
            messages: Messages to estimate

        Returns:
            Estimated token count (simplified: ~4 chars per token)
        """
        total_chars = sum(len(msg.get("content", "")) for msg in messages)
        return total_chars // 4

    def should_trim(self, messages: List[Dict[str, str]]) -> bool:
        """Check if trimming is needed.

        Args:
            messages: Message history

        Returns:
            True if trimming needed
        """
        return len(messages) > self.max_messages

    def reset(self):
        """Reset counters."""
        self.trim_count = 0
        self.assemble_count = 0

    def get_stats(self) -> Dict[str, Any]:
        """Get CM statistics.

        Returns:
            Statistics dict
        """
        return {
            "name": self.name,
            "trim_count": self.trim_count,
            "assemble_count": self.assemble_count,
            "max_messages": self.max_messages,
        }
