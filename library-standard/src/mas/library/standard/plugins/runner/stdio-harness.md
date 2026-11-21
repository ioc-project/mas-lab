<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# stdio-harness — an agent runner over standard input and output

| Field | Value |
|-------|--------|
| **ID** | `stdio-harness` |
| **Type** | `agent_runner` |
| **URN** | `mas.agent_runner.stdio-harness` |
| **Implementation** | `StdioHarnessRunner` in `mas.library.standard.plugins.runner.stdio_harness` |
| **Manifest** | `spec.runner: {type: stdio-harness, params: {...}}` on an Agent |

Runs an agent's tasks by driving an external command: the task goes to its stdin, the reply comes
back on its stdout. MAS Lab keeps the boundary (the A2A listener, the Controls, the events); the
command runs its own loop, so there is no per-tool control inside it.

| Param | Default | Meaning |
| --- | --- | --- |
| `command` | required | The argument list. A list, never a shell string. `command[0]` is an absolute path. |
| `allow` | required | The executables this runner may start. `command[0]` must be one of them (compared after resolving links). |
| `env` | `{}` | Exactly the environment the child gets. Nothing is inherited, not even `PATH`. |
| `cwd` | none | Working directory of the child. |
| `framing` | `line` | `line`: the task is one line (a newline is written `\n`, a backslash `\\`), the reply is one line. `jsonrpc`: newline-delimited JSON-RPC 2.0. |
| `lifecycle` | `per-task` | `per-task`: one process per task, ended after the reply. `persistent`: one process, tasks one at a time, respawned if it died. |
| `timeout` | `60` | Seconds to wait for the reply. The process group is killed on expiry. |
| `max_task_bytes`, `max_reply_bytes` | 1 MiB | Caps on the request and on all the output of one task. |
| `term_grace` | `2` | Seconds between SIGTERM and SIGKILL when the process is ended. |

`jsonrpc` sends `{"jsonrpc": "2.0", "id": N, "method": "task", "params": {"text", "session_id", "task_id",
"caller", "headers"}}`. The harness may write `{"method": "event", "params": {...}}` notifications, which come back as
the reply's events, and ends with `{"id": N, "result": {"text", "artifacts", "events", "task_state"}}` or
`{"id": N, "error": {"message"}}`.

```yaml
spec:
  runner:
    type: stdio-harness
    params:
      command: [/usr/local/bin/some-harness, --stdio]
      allow: [/usr/local/bin/some-harness]
      framing: jsonrpc
      env: {HARNESS_MODE: batch}
```

[Agent runner](../../../../../../../docs/references/agent-runner.md).
