<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# stack — compose agent_comm layers

| Field | Value |
|-------|--------|
| **ID** | `stack` |
| **Alias** | `stack` |
| **Kind** | `agent_comm` |
| **URN** | `mas.agent_comm.stack` |
| **Implementation** | `StackAgentComm` in `mas.library.standard.plugins.agentcomm.stack` |
| **Manifest keys** | `spec.agent_comm` (`type: stack`, `params.layers`) |

A link at the bottom, decorator layers above it. Start is bottom first;
stop is top first. The stack honours the bottom link's send intents.
Off by default (`default_enabled: false`).

[Communication stack](../../../../../../../docs/references/comm-stack.md).
