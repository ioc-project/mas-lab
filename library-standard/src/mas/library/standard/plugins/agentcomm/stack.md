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
| **Manifest keys** | none: a Flavour `bind.agent_comm` chain builds one per agent (`StackHandle`); from Python, `StackAgentComm(layers=[...])` |

The agent-call adapter over the generic `Stack`. The first layer is a
transport (a link with `send(target, task, ...)`, or a `Layer` with
`needs_lower = False`); the layers above are generic layers such as the
`a2a-framing` layer, or `BoundLayer` decorators (deprecated, still work).
`{type, params}` entries resolve as an `agent_comm` link first, then as a
`layer` (`resolve_layer_class`). So `a2a` is the A2A SDK link, which can
only be the first entry, and `a2a-framing` is the framing layer, which cannot
be. Start is bottom
first; stop is top first. Capabilities OR-merge. Every send gets a child
W3C `traceparent`. A send intent is honoured when the bottom link and
every layer that declares intents honour it (`a2a` honours `task` only).
`serve(handler)` builds the inbound chain, top layer first, and gives it
to the bottom link. `send` / `asend` raise `AgentCommError` for every
failure: a layer's `StackError` as `StackSendError`, with the layer's
error as the cause. Off by default (`default_enabled: false`).

A Flavour `bind.agent_comm` chain of infra rows builds one of these per
local agent ([flavour.md](../../../../../../../docs/manifests/flavour.md#bind)).

[Communication stack](../../../../../../../docs/references/comm-stack.md).
