<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Messaging and orchestration contracts

This page covers agent-as-tool delegation. Topology and workflow are declarative
YAML; the kernel and `EngineContract` run them.

## DelegationContract

`DelegationContract` (`mas.runtime.boundary.delegation.protocol`) executes peer
delegation tools surfaced by MAS `workflow.delegates_to`.

| Method | Notes |
| --- | --- |
| `delegate(target_agent_id, task, ...)` | Run a task on a peer agent |
| `call_delegate_tool(tool_name, arguments, ...)` | Dispatch a `delegate_to_*` tool |
| `is_delegate_tool(tool_name)` | Whether the name is a delegate tool |
| `adelegate(...)` / `acall_delegate_tool(...)` | Async variants |

```python
from mas.runtime.boundary.delegation.protocol import DelegationContract
```
