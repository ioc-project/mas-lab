<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# local-group — in-process group link

| Field | Value |
|-------|--------|
| **ID** | `local-group` |
| **Alias** | `local-group`, `local_group` |
| **Kind** | `agent_comm` |
| **URN** | `mas.agent_comm.local_group` |
| **Implementation** | `LocalGroupAgentComm` in `mas.library.standard.plugins.agentcomm.local_group` |
| **Manifest keys** | `spec.agent_comm` (`type: local-group`) |

Send reaches one peer, any instance of a service, or a room. Pass a
shared `LocalGroupFabric` from Python; two links given only a name
cannot talk. Off by default (`default_enabled: false`).

[Communication stack](../../../../../../../docs/references/comm-stack.md).
