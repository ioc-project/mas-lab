#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regressions found in review: retry policy per call kind, subagent settings, deprecated cache kind."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.library.standard.plugins.layers.retry import RetryLayer
from mas.library.standard.plugins.system_tools.spawn_subagent import SubagentsPlugin
from mas.runtime.registry.provider_protocol import ManifestToolLoadError


def test_a_retry_layer_for_both_kinds_retries_a_tool_only_when_it_is_safe() -> None:
    layer = RetryLayer.wrap(object(), None, call_kinds=("llm", "tool"))
    tool = layer._policy_of(SimpleNamespace(op="TOOL_CALL"))  # type: ignore[arg-type]
    llm = layer._policy_of(SimpleNamespace(op="LLM_CALL"))  # type: ignore[arg-type]
    assert tool.require_idempotent and tool.max_attempts == 2
    assert not llm.require_idempotent and llm.max_attempts == 4


def test_a_retry_layer_params_apply_to_both_policies() -> None:
    layer = RetryLayer.wrap(object(), {"max_attempts": 7}, call_kinds=("llm", "tool"))
    assert layer.policy.max_attempts == 7
    assert layer.tool_policy.max_attempts == 7 and layer.tool_policy.require_idempotent


@pytest.mark.parametrize(
    "settings",
    [
        {"max_depth": None},
        {"max_depth": -1},
        {"max_spawns": "x"},
        {"max_spawns": True},
        {"templates": "abc"},
        {"templates": [{"ref": "x.yaml"}]},
    ],
)
def test_a_bad_subagents_setting_is_a_load_error(settings: dict) -> None:
    with pytest.raises(ManifestToolLoadError):
        SubagentsPlugin(**settings)


def test_subagents_max_spawns_none_is_unlimited() -> None:
    plugin = SubagentsPlugin(max_spawns=None, max_depth=2, templates=[{"id": "a"}])
    assert plugin._ledger().max_spawns is None
