#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from library_ioa import package_root
from library_ioa.plugins.mcp import MCPClientFactory, MCPProviderSpec, MCPToolProvider
from mas.runtime.registry import get_registry


def test_package_root_finds_library_yaml() -> None:
    root = package_root()
    assert (root / "library.yaml").is_file()


def test_plugin_package_exports() -> None:
    assert MCPToolProvider is not None
    assert MCPClientFactory is not None
    assert MCPProviderSpec is not None


def test_urn_registry_resolves_mcp_shortcut() -> None:
    info = get_registry().resolve_by_type("tool_provider", "mcp")
    assert info is not None
    assert info.load_class() is MCPToolProvider


def test_every_new_transport_and_framing_layer_is_a_layer() -> None:
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from library_ioa.plugins.a2a.layer import A2ALayer
    from library_ioa.plugins.http_layer import HttpLayer
    from library_ioa.plugins.slim import SlimAgentComm
    from mas.library.standard.lib.stack.handle import is_transport, layer_classes

    registry = get_registry()
    for name, cls in (("slim", SlimAgentComm), ("http", HttpLayer), ("a2a-framing", A2ALayer)):
        info = registry.resolve_by_type("layer", name)
        assert info is not None and info.load_class() is cls, name
    assert is_transport(SlimAgentComm) and is_transport(HttpLayer) and not is_transport(A2ALayer)
    assert callable(SlimAgentComm.shared_service) and callable(SlimAgentComm.agent_name)
    assert layer_classes([{"type": "slim", "params": {}}, {"type": "a2a-framing", "params": {}}]) == [
        SlimAgentComm,
        A2ALayer,
    ]
    assert registry.resolve_by_type("agent_comm", "a2a").load_class() is A2AAgentComm


def test_a2a_is_the_sdk_link_and_a2a_framing_is_the_framing_layer() -> None:
    """The two ids resolve on the real registry; a chain must start at a transport and stack nothing over one."""
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from library_ioa.plugins.a2a.layer import A2ALayer
    from mas.library.standard.lib.stack import StackError
    from mas.library.standard.lib.stack.handle import is_direct_link, layer_classes

    a2a = {"type": "a2a", "params": {}}
    framing = {"type": "a2a-framing", "params": {}}
    node = {"type": "slim", "params": {}}
    assert layer_classes([a2a]) == [A2AAgentComm]
    assert layer_classes([node, framing])[1] is A2ALayer
    with pytest.raises(StackError, match="the first layer must be a transport; 'a2a-framing' needs a layer below it"):
        layer_classes([framing])
    with pytest.raises(StackError, match=r"layer 1 \('a2a'\) is a transport; only the first layer can be one"):
        layer_classes([node, a2a])
    assert is_direct_link(A2AAgentComm)  # a one-row chain on it is the released direct path
    from library_ioa.plugins.slim import SlimAgentComm
    from mas.library.standard.plugins.agentcomm.local_group import LocalGroupAgentComm

    assert not is_direct_link(SlimAgentComm) and not is_direct_link(LocalGroupAgentComm)
