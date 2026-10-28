#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The kernel turns OperatorSteerReceived into context assembly for every pattern."""

from __future__ import annotations

from mas.library.standard.plugins.design_patterns.cot import CotPlugin
from mas.library.standard.plugins.design_patterns.introspection import IntrospectionPlugin
from mas.library.standard.plugins.design_patterns.linear import DeterministicLinearPlugin
from mas.library.standard.plugins.design_patterns.parallel import DeterministicParallelPlugin
from mas.library.standard.plugins.design_patterns.plan_execute import PlanExecutePlugin
from mas.library.standard.plugins.design_patterns.react import ReactPlugin
from mas.library.standard.plugins.design_patterns.single import DeterministicSingleAgentPlugin
from mas.library.standard.plugins.design_patterns.single_pass import SinglePassPlugin
from mas.library.standard.plugins.design_patterns.tree_of_thoughts import TreeOfThoughtsPlugin
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.state import DpState
from mas.runtime.schema.egress import RequestCtxAssembly
from mas.runtime.schema.ingress import OperatorSteerReceived

_PLUGINS = (
    ReactPlugin,
    SinglePassPlugin,
    CotPlugin,
    TreeOfThoughtsPlugin,
    PlanExecutePlugin,
    IntrospectionPlugin,
    DeterministicLinearPlugin,
    DeterministicParallelPlugin,
    DeterministicSingleAgentPlugin,
)


def test_kernel_applies_steer_for_every_pattern() -> None:
    event = OperatorSteerReceived(
        steer_id="steer-1",
        context_text="go via Lyon",
        source="peer",
        sender="planner",
        intent="steer",
    )
    for cls in _PLUGINS:
        plugin = cls()
        kernel = RuntimeKernel(plugin=plugin)
        result = kernel.transition(event)
        assert kernel.q.dp is DpState.CTX_BUILD, plugin.plugin_id
        assert isinstance(result.egress[-1], RequestCtxAssembly), plugin.plugin_id
        assert result.egress[-1].collect_id == "steer-1"
        assert result.egress[-1].operator_context == "go via Lyon"
        leftover = plugin.handle_event(kernel.q, kernel.run, event, config=kernel.config)
        assert not any(isinstance(item, RequestCtxAssembly) for item in leftover), plugin.plugin_id
