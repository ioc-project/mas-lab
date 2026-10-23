<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Open standards

<p class="eyebrow">Protocols MAS-Lab speaks</p>

MAS-Lab talks to the rest of the stack over open protocols. The agent, the
team, and the experiment stay in YAML. The protocol is an infrastructure
choice: swap the adapter and the spec does not change.

This section is the map. Each page says what the protocol is, how MAS-Lab
implements it, which packages you install, what compliance evidence exists,
and where the tutorials live.

| Protocol | What it carries | MAS-Lab surface |
| --- | --- | --- |
| [OpenAI Chat Completions](openai.md) | Model calls | `standard:openai` and any OpenAI-compatible proxy |
| [MCP](mcp.md) | Remote tools | `mas-library-ioa`, `mas-mcp` |
| [A2A](a2a.md) | Remote agents | `mas-library-ioa`, `mas-ctl serve` |
| [Agent Skills](agent-skills.md) | Reusable instruction packs | `mas-library-skills`, [agentskills.io](https://agentskills.io/) |
| [OpenTelemetry](opentelemetry.md) | Traces | `mas-library-telemetry` and AGNTCY's `ioa-observe-sdk` |

Protocols sit in infra, not in agent logic. You can develop with local tools
and local delegation, then point the same agent at an MCP server or an A2A
peer. [Tutorial 4](../tutorials/04-mcp-tools/README.md) and
[Tutorial 5](../tutorials/05-a2a-agents/README.md) show that split.

The landing page lists the same five protocols. Hands-on paths stay under
[Discover](../discover/index.md). Flag tables and YAML kinds stay under
[References](../references/index.md).
