#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock Design Pattern contract for testing."""

from typing import Any, Dict, List, Optional


class MockDPContract:
    """Mock design pattern that simulates execution steps."""

    def __init__(
        self,
        name: str = "mock_dp",
        num_steps: int = 3,
        responses: Optional[List[str]] = None,
        should_fail_at: Optional[int] = None,
        **kwargs,
    ):
        """Initialize mock DP.

        Args:
            name: DP name
            num_steps: Number of execution steps to simulate
            responses: Responses for each step
            should_fail_at: Step number where DP should fail (0-indexed)
            **kwargs: Additional config
        """
        super().__init__()
        self.name = name
        self.num_steps = num_steps
        self.responses = responses or [f"Step {i} response" for i in range(num_steps)]
        self.should_fail_at = should_fail_at
        self.current_step = 0
        self.execution_history: List[Dict[str, Any]] = []

    def execute(self, state: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Execute one step of the mock DP.

        Args:
            state: Current agent state (dict with 'messages' and 'metadata')
            **kwargs: Additional parameters

        Returns:
            Updated agent state

        Raises:
            RuntimeError: If should_fail_at matches current step
        """
        # Record execution
        self.execution_history.append({"step": self.current_step, "state": state, "kwargs": kwargs})

        # Check for simulated failure
        if self.should_fail_at is not None and self.current_step == self.should_fail_at:
            raise RuntimeError(f"Mock DP failed at step {self.current_step}")

        # Update state
        response = self.responses[self.current_step % len(self.responses)]

        # Create new state with response
        messages = state.get("messages", [])
        metadata = state.get("metadata", {})

        new_state = {
            "messages": messages + [{"role": "assistant", "content": response}],
            "metadata": {**metadata, "dp_step": self.current_step, "dp_name": self.name},
        }

        self.current_step += 1

        # Mark as done if reached max steps
        if self.current_step >= self.num_steps:
            new_state["metadata"]["done"] = True

        return new_state

    def is_complete(self, state: Dict[str, Any]) -> bool:
        """Check if DP execution is complete.

        Args:
            state: Current agent state

        Returns:
            True if complete
        """
        metadata = state.get("metadata", {})
        return metadata.get("done", False) or self.current_step >= self.num_steps

    def reset(self):
        """Reset execution state."""
        self.current_step = 0
        self.execution_history.clear()

    def get_config(self) -> Dict[str, Any]:
        """Get DP configuration.

        Returns:
            Configuration dict
        """
        return {"name": self.name, "num_steps": self.num_steps, "current_step": self.current_step}
