#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Delegation boundary — workflow policy and the DelegationContract protocol."""

from mas.runtime.boundary.delegation.handoff import handoff_target
from mas.runtime.boundary.delegation.policy import (
    DELEGATE_TOOL_PREFIX,
    INTENT_HEADER,
    SEND_INTENTS,
    SEND_SEMANTICS,
    SEND_TOOL_NAME,
    delegate_tool_name,
    delegation_targets,
    entry_agent_id,
    openai_delegation_tools,
    parse_delegate_tool_name,
    uses_llm_peer_delegation,
)
from mas.runtime.boundary.delegation.protocol import DelegationContract

__all__ = [
    "DELEGATE_TOOL_PREFIX",
    "DelegationContract",
    "INTENT_HEADER",
    "SEND_INTENTS",
    "SEND_SEMANTICS",
    "SEND_TOOL_NAME",
    "delegate_tool_name",
    "delegation_targets",
    "entry_agent_id",
    "handoff_target",
    "openai_delegation_tools",
    "parse_delegate_tool_name",
    "uses_llm_peer_delegation",
]
