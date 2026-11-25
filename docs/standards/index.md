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
| [Open Responses](openresponses.md) | Model calls | `standard:openresponses` |
| [MCP](mcp.md) | Remote tools | `library-ioa/`, `mas-mcp` |
| [A2A](a2a.md) | Remote agents | `library-ioa/`, `mas-ctl serve` |
| [Agent Skills](agent-skills.md) | Reusable instruction packs | `library-skills/`, [agentskills.io](https://agentskills.io/) |
| [OpenTelemetry](opentelemetry.md) | Traces | `library-telemetry/` and AGNTCY's `ioa-observe-sdk` |

Protocols sit in infra, not in agent logic. You can develop with local tools
and local delegation, then point the same agent at an MCP server or an A2A
peer. [Tutorial 4](../tutorials/04-mcp-tools/README.md) and
[Tutorial 5](../tutorials/05-a2a-agents/README.md) show that split.

The landing page lists the same protocols. Hands-on paths stay under
[Discover](../discover/index.md). Flag tables and YAML kinds stay under
[References](../references/index.md).

## Other useful references

These are not open standards. MAS-Lab uses them as data or tooling.

### [models.dev](https://models.dev)

A community catalog of model facts (context windows, output limits,
modalities, pricing). Source:
[anomalyco/models.dev](https://github.com/anomalyco/models.dev). API:
`https://models.dev/api.json`. MIT (`Copyright (c) 2025 models.dev`).
Compatible with this repository's Apache-2.0 license; the required
copyright and permission notice is in `NOTICE` at the repository root.

MAS-Lab does not fetch it at run time and does not vendor the full dump.
`scripts/sync_model_catalog.py` copies context, max output, modalities, and
pricing onto curated rows in the
[LLM model catalog](../manifests/llm-model-catalog.md). Hand-written
settings stay as written. A sync unions modalities with the catalog list
(so a curated `pdf` is not dropped when models.dev omits it) and does not
write an empty list over a missing field. The runtime then gates image and
PDF parts against those lists.
