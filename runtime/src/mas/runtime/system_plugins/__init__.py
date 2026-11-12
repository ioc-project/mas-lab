#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""System plugins: the syscalls a model may emit."""

from mas.runtime.system_plugins.contract import (
    CallContext,
    InstanceSystemPlugin,
    SessionContext,
    SystemPluginBase,
    ToolDefinition,
    ToolInstance,
    UnknownSystemToolError,
)
from mas.runtime.system_plugins.load import (
    BoundSystemPlugins,
    load_system_plugins,
    parse_system_plugin_item,
    require_entry,
    warn_deprecated_once,
)
from mas.runtime.system_plugins.spawn_ledger import SpawnLedger

__all__ = [
    "BoundSystemPlugins",
    "CallContext",
    "InstanceSystemPlugin",
    "SessionContext",
    "SpawnLedger",
    "SystemPluginBase",
    "ToolDefinition",
    "ToolInstance",
    "UnknownSystemToolError",
    "load_system_plugins",
    "parse_system_plugin_item",
    "require_entry",
    "warn_deprecated_once",
]
