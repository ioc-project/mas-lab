<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# send — peer handoff executor

| Field | Value |
|-------|--------|
| **ID** | `send` |
| **Alias** | `send` |
| **Kind** | `system_tool` / `engine_tool_provider` (`delegation`) |
| **URN** | `mas.system_tool.send` |
| **Implementation** | `SendTool` in `mas.library.standard.plugins.system_tools.send` |
| **Manifest keys** | `spec.tools` (`kind: system`, `name: send`) |

`default_enabled: false`. Declaring this system tool does not add a tool
named `send` to the model's list; `list_tools` still returns only
`delegate_to_<id>`. A Python `send` call uses the same path.
A user tool named `send` stays that tool.

[Communication stack](../../../../../../../docs/references/comm-stack.md).
[System tools](../../../../../../../docs/manifests/system-tools.md).
