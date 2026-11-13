#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library-standard governance plugins."""

from mas.library.standard.plugins.governance.debug_script import DebugScriptPlugin
from mas.library.standard.plugins.governance.no_undeclared_peer import (
    NoUndeclaredPeerPlugin,
    undeclared_peer_observation,
)
from mas.library.standard.plugins.governance.no_undeclared_tool import (
    NoUndeclaredToolPlugin,
    undeclared_tool_observation,
)
from mas.library.standard.plugins.governance.sample import SampleGovernancePlugin

__all__ = [
    "DebugScriptPlugin",
    "NoUndeclaredPeerPlugin",
    "NoUndeclaredToolPlugin",
    "SampleGovernancePlugin",
    "undeclared_peer_observation",
    "undeclared_tool_observation",
]
