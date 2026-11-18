<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# a2a-harness — an agent runner that drives a remote A2A agent

| Field | Value |
|-------|--------|
| **ID** | `a2a-harness` |
| **Type** | `agent_runner` |
| **URN** | `mas.agent_runner.a2a-harness` |
| **Implementation** | `A2AHarnessRunner` in `library_ioa.plugins.a2a.harness` |
| **Manifest** | `spec.runner: {type: a2a-harness, params: {url: ...}}` on an Agent |

Runs an agent's tasks by sending each one to a remote A2A agent and returning its reply. The agent is
then a MAS Lab front for that remote agent: the A2A listener, the Controls and the events are MAS Lab's,
and the remote agent runs its own loop.

| Param | Default | Meaning |
| --- | --- | --- |
| `url` | required | The remote agent's A2A url. Pass `url` or `comm`, not both. |
| `target` | `harness` | The destination name handed to the link (a stack may route on it). |
| `headers` | none | Handed to the `a2a` link. |
| `timeout` | 10 s | Seconds the `a2a` link waits for the reply; raise it for a slow harness. |
| `comm` | none | In code only: any `AgentCommContract`, such as a built `agent_comm/stack`, so the layers above the transport apply to the task as to a peer send. |

The task's session id is the A2A `contextId`, so a conversation continues on the remote agent. A failed,
rejected or canceled remote task is a runner error, not reply text. Only text comes back, so the remote
agent's own tool events are not visible. The `a2a` link does not send per-call headers.

```yaml
spec:
  runner:
    type: a2a-harness
    params:
      url: http://127.0.0.1:9100
      timeout: 120
```

[Agent runner](../../../../../docs/references/agent-runner.md),
[a2a link](a2a.md).
