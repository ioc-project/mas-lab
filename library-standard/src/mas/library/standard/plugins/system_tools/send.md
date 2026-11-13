<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# send — peer handoff executor

| Field | Value |
|-------|--------|
| **ID** | `send` |
| **Alias** | `send` |
| **Kind** | `system_plugin` |
| **URN** | `mas.system_plugin.send` |
| **Implementation** | `SendPlugin` / `SendTool` in `mas.library.standard.plugins.system_tools.send` |
| **Manifest keys** | `spec.system_plugins` (`send`) |

`default_enabled: false`. Naming this plugin does not add a tool
named `send` to the model's list; `list_tools` still returns only
`delegate_to_<id>`. A Python `send` call uses the same path.
A user tool named `send` stays that tool. A user tool named `delegate_to_<id>`
stays that tool unless that leftover name is offered. `delegates_to` is that
offer list, not an execute lock. `with-hardened` names `gov_no_undeclared_peer`
so leftover send to another dest is BLOCK at egress.

[Communication stack](../../../../../../../docs/references/comm-stack.md).
[System plugins](../../../../../../../docs/manifests/system-plugins.md).
