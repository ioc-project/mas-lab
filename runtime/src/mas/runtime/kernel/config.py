#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kernel configuration — profile flags and per-run execution limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mas.runtime.agent_defaults import default_pattern_plugin_id
from mas.runtime.schema.governance import GovIngressProfile, GovPolicyProfile
from mas.runtime.spec.defaults import DEFAULT_ENGINE_QUEUE_DEPTH, DEFAULT_MAX_AUTO_STEPS

if TYPE_CHECKING:
    from mas.runtime.boundary.gov.policy_engine import GovernancePolicyEngine


@dataclass(frozen=True)
class KernelConfig:
    pattern_plugin_id: str = field(default_factory=default_pattern_plugin_id)
    gov_policy_profile: GovPolicyProfile = GovPolicyProfile.BLOCK_DESTRUCTIVE
    gov_ingress_profile: GovIngressProfile = GovIngressProfile.PERMISSIVE
    gov_block_destructive: bool = False
    gov_trigger_destructive: bool = False
    max_gov_retries: int = 2
    hitl_on_tool: bool = False
    hitl_on_tool_result: bool = False
    hitl_once_per_turn: bool = False
    egress_governance_plugin: object | None = field(default=None, compare=False)
    enable_memory_egress: bool = False
    enable_transport_egress: bool = False
    max_cot_pass: int = 1
    parallel_tool_calls: bool = True
    engine_queue_depth: int = DEFAULT_ENGINE_QUEUE_DEPTH
    max_auto_steps: int | None = DEFAULT_MAX_AUTO_STEPS
    policy_engine: GovernancePolicyEngine | None = field(default=None, compare=False)
    ingress_governance_plugins: tuple = field(default=(), compare=False)
    enable_governance: bool = True
    enable_envelope_observability: bool = True
    # gdb and checkpoint helpers. They observe; they never BLOCK.
    # Off unless config.yaml plugins / spec.debug enable them.
    # The agent manifest's own spec: block (manifest["spec"]), for governance
    # plugins that need to reason about the agent's declared tools/context/
    # models rather than just the live transition stream. None when built
    # outside spec parsing (e.g. tests constructing KernelConfig directly).
    agent_spec: dict[str, Any] | None = field(default=None, compare=False)
    # Optional sink: Session.on_governance_decision. compare=False so tests
    # constructing KernelConfig() still match. Called after authorize/validate.
    on_decision_snapshot: Any | None = field(default=None, compare=False, repr=False)
