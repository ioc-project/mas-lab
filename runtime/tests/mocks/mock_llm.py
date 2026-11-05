#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock LLM and Model contracts for testing."""

from typing import Any, Dict, List, Optional


class MockModelContract:
    """Mock model contract that returns predefined responses."""

    def __init__(self, model_id: str = "mock-model", responses: Optional[List[str]] = None, **kwargs):
        """Initialize mock model.

        Args:
            model_id: Model identifier
            responses: List of responses to return in sequence
            **kwargs: Additional config
        """
        super().__init__()
        self.model_id = model_id
        self.responses = responses or ["Mock response"]
        self.call_count = 0
        self.call_history: List[Dict[str, Any]] = []

    def generate(
        self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 2000, **kwargs
    ) -> str:
        """Generate mock response.

        Args:
            messages: Conversation messages
            temperature: Temperature parameter (ignored)
            max_tokens: Max tokens (ignored)
            **kwargs: Additional parameters

        Returns:
            Next response from the predefined list
        """
        # Record call
        self.call_history.append(
            {"messages": messages, "temperature": temperature, "max_tokens": max_tokens, "kwargs": kwargs}
        )

        # Return next response (cycle if exhausted)
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return response

    def generate_stream(
        self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 2000, **kwargs
    ):
        """Generate streaming mock response.

        Args:
            messages: Conversation messages
            temperature: Temperature parameter (ignored)
            max_tokens: Max tokens (ignored)
            **kwargs: Additional parameters

        Yields:
            Response chunks
        """
        response = self.generate(messages, temperature, max_tokens, **kwargs)
        # Yield in chunks
        chunk_size = 10
        for i in range(0, len(response), chunk_size):
            yield response[i : i + chunk_size]

    def reset(self):
        """Reset call counter and history."""
        self.call_count = 0
        self.call_history.clear()


class MockLLMContract:
    """Mock LLM contract wrapper for compatibility."""

    def __init__(self, model: Optional[MockModelContract] = None):
        """Initialize with optional model.

        Args:
            model: Mock model contract
        """
        self.model = model or MockModelContract()

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Chat completion.

        Args:
            messages: Conversation messages
            **kwargs: Additional parameters

        Returns:
            Mock response
        """
        return self.model.generate(messages, **kwargs)

    def completion(self, prompt: str, **kwargs) -> str:
        """Text completion.

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters

        Returns:
            Mock response
        """
        messages = [{"role": "user", "content": prompt}]
        return self.model.generate(messages, **kwargs)
