<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# a2a (agent_comm) — the A2A SDK client at a url

| Field | Value |
|-------|--------|
| **ID** | `a2a` |
| **Kind** | `agent_comm` (a link: the bottom of a chain or an `agent_comm/stack`) |
| **URN** | `mas.agent_comm.a2a` |
| **Implementation** | `A2AAgentComm` in `library_ioa.plugins.a2a.agent_comm` |
| **Manifest keys** | an agent-keyed Application row `{protocol: a2a, url}` (`--connect NAME=a2a://...`); a Flavour chain of one A2A server row, `bind.agents.<id>.agent_comm: [outage-server]` |

Sends each delegation as an A2A `SendMessage` to the server at `url`
(`headers`, `timeout` are its other params) through the A2A SDK client,
and returns the agent's reply. It honours the `task` intent only.

`a2a` is this link, a transport, so it is only the bottom of a chain. The layer
that frames A2A over another transport is a different id,
[`a2a-framing`](a2a-framing.md), whose requests are MAS Lab's own JSON-RPC and
not this SDK client. A link like this one reaches
one agent at its own url and shares nothing across agents (it is
no `ChainMember`, so `is_direct_link` holds), so a chain of that one row is the released direct path,
the same as an agent-keyed row: ctl writes it as the agent's row, and
`usage: listen` serves the agent at that url through the `a2a` exposure.

[A2A internals](../../../../../docs/a2a/developer.md),
[Communication stack](../../../../../docs/references/comm-stack.md#stack).
