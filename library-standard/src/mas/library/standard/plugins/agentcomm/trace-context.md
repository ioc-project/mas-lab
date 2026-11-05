<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# trace_context — child correlation id on send (deprecated)

| Field | Value |
|-------|--------|
| **ID** | `trace_context` |
| **Alias** | `trace_context`, `trace-context` |
| **Kind** | `agent_comm` |
| **URN** | `mas.agent_comm.trace_context` |
| **Implementation** | `TraceContextLayer` in `mas.library.standard.plugins.agentcomm.trace_context` |
| **Manifest keys** | none (deprecated): every stack stamps the `traceparent`; from Python it is still a `BoundLayer` in `StackAgentComm(layers=[...])` |

Deprecated: every stack now stamps a child `traceparent` on each send,
so this layer is not needed. It still works.

Writes a W3C `traceparent` on each send: same trace id as a valid parent,
new span id. That id is a correlation id, not an OpenTelemetry span.
Off by default (`default_enabled: false`).

[Communication headers](../../../../../../../docs/references/comm-headers.md).
