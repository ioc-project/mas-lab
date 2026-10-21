#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plugin contracts — Mealy hook Protocols and capability/governance taxonomy."""

from mas.runtime.contracts.base import (
    BasePlugin,
    CapabilityContract,
    ContractBase,
    ContractRegistry,
    GovernanceContract,
    OrchestrationContract,
    PolicyViolation,
)
from mas.runtime.contracts.cm_factory import CMFactory
from mas.runtime.contracts.context_contract import (
    ContextContract,
    ContextPart,
    ContextPlacement,
    ContextProvenance,
    ContextResolver,
)
from mas.runtime.contracts.context_manager_contract import ContextManagerContract
from mas.runtime.contracts.memory_contract import MemoryContract
from mas.runtime.contracts.protocols import (
    CtxAssembler,
    CtxAssemblerContract,
    DesignPatternContract,
    DesignPatternPlugin,
    EngineContract,
    HitlResponder,
    MealyPlugin,
    ObservabilitySink,
)
from mas.runtime.contracts.recorder_contract import RecorderContract
from mas.runtime.contracts.summarizer_contract import SummarizerContract
from mas.runtime.contracts.tool_contract import (
    ToolContract,
    ToolResultEnvelope,
    invoke_call_tool,
    overlay_tool_advertise,
    tool_fixture,
)

__all__ = [
    "BasePlugin",
    "CMFactory",
    "CapabilityContract",
    "ContextContract",
    "ContextManagerContract",
    "ContextPart",
    "ContextPlacement",
    "ContextProvenance",
    "ContextResolver",
    "ContractBase",
    "ContractRegistry",
    "CtxAssembler",
    "CtxAssemblerContract",
    "DesignPatternContract",
    "DesignPatternPlugin",
    "EngineContract",
    "GovernanceContract",
    "HitlResponder",
    "MealyPlugin",
    "MemoryContract",
    "ObservabilitySink",
    "OrchestrationContract",
    "PolicyViolation",
    "RecorderContract",
    "SummarizerContract",
    "ToolContract",
    "ToolResultEnvelope",
    "invoke_call_tool",
    "overlay_tool_advertise",
    "tool_fixture",
]
