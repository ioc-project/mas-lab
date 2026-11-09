#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern plugins registered and expose protocol lines."""

from mas.library.standard.plugins.design_patterns.cot import CotPlugin
from mas.library.standard.plugins.design_patterns.introspection import IntrospectionPlugin
from mas.library.standard.plugins.design_patterns.react import ReactPlugin
from mas.library.standard.plugins.design_patterns.tree_of_thoughts import (
    TreeOfThoughtsPlugin,
    _parse_thoughts,
)
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct
from mas.runtime.registry import get_registry


def test_all_design_patterns_registered():
    registry = get_registry()
    for name in ("react", "cot", "tree_of_thoughts", "plan_execute", "single_pass", "introspection"):
        assert registry.resolve(name) is not None, name


def test_react_plugin_registered_and_instantiable():
    plugin = ReactPlugin()
    assert plugin.plugin_id == "react@v1"


def test_cot_resets_pass_on_user_input():
    plugin = CotPlugin()
    assert plugin.plugin_id == "cot@v1"


def test_tot_parse_thoughts_json_list():
    raw = '{"thoughts": [{"content": "a", "score": 0.9}]}'
    thoughts = _parse_thoughts(raw)
    assert thoughts[0]["content"] == "a"


def test_tot_protocol_generate_phase():
    plugin = TreeOfThoughtsPlugin()
    q = QProduct()
    q.dp_data = {"tot_phase": "GENERATE"}
    lines = plugin.protocol_lines(q)
    assert any("TREE-OF-THOUGHTS" in line for line in lines)


def test_introspection_forces_min_two_passes():
    plugin = IntrospectionPlugin()
    assert plugin.plugin_id == "introspection@v1"
    assert isinstance(plugin, CotPlugin)


def test_plugins_accept_kernel_config():
    _ = KernelConfig(max_cot_pass=2)
    plugin = ReactPlugin()
    assert plugin.plugin_id.endswith("@v1")
