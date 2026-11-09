#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mock implementations of MAS contracts for testing.

This module provides mock implementations of all contract interfaces,
allowing unit tests for orchestration code without real LLM calls or
filesystem operations.
"""

from .mock_cm import MockContextManagerContract
from .mock_dp import MockDPContract
from .mock_llm import MockLLMContract, MockModelContract
from .mock_tools import MockToolContract, MockToolProvider

__all__ = [
    "MockLLMContract",
    "MockModelContract",
    "MockToolContract",
    "MockToolProvider",
    "MockDPContract",
    "MockContextManagerContract",
]
