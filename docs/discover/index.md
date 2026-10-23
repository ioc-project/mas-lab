<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Discover

<p class="eyebrow">Tutorials, labs, and IoC integrations</p>

This section is the hands-on path: IoC integrations, tutorials, and labs.

The left menu lists every tutorial, every lab family, and the integration
pages. Use this index to pick a route. Open protocols (OpenAI, MCP, A2A,
Agent Skills, OpenTelemetry) live under
[Open standards](../standards/index.md).

<div class="grid cards" markdown>

-   **IoC integrations**

    ---

    How Cognition Protocol skills go in, and how run records come out for
    SCOPE.

    [IoC integrations](integrations.md)

-   **Open standards**

    ---

    OpenAI Chat Completions, MCP, A2A, Agent Skills, and OpenTelemetry
    through AGNTCY's observe-sdk.

    [Open standards](../standards/index.md)

-   **Tutorials**

    ---

    From one agent to a governed experiment. Start with
    [Tutorial 0](../tutorials/00-environment-setup/README.md).

    [Tutorials index](../tutorials/index.md)

-   **Labs**

    ---

    Packaged experiments: IoC motivation and protocol studies, plus the three
    paper labs shipped with this repository.

    [Labs index](labs.md)

</div>

## IoC integrations

| Path | What it is |
| --- | --- |
| [Cognition Protocols as skills](integrations.md#cognition-protocols-go-in-as-skills) | ACCORD and CONCORD enter as initiator and receiver skills |
| [Runs out to SCOPE](integrations.md#runs-come-out-for-scope) | Live telemetry or conversion from a recorded run |
| [Knowledge graph locally](integrations.md#a-knowledge-graph-without-the-platform) | Build the same graph SCOPE would, on your machine |

## Tutorials

Do Tutorials 0 to 3 in order: install, then one agent, then a MAS, then
experiments. Tutorials 4 to 6 and 8 to 14 each cover one capability.

| # | Tutorial | What you learn |
| --- | --- | --- |
| 0 | [Environment setup](../tutorials/00-environment-setup/README.md) | Install, PATH, model endpoint, API key wiring |
| 1 | [Build an agent](../tutorials/01-building-an-agent/README.md) | Agent manifest, tools, skills, memory, CLI, traces |
| 2 | [Orchestrate your MAS](../tutorials/02-creating-a-mas/README.md) | MAS manifest, delegation, topology overlays |
| 3 | [Run an experiment](../tutorials/03-experiments-and-analysis/README.md) | Experiments, benchmarks, pipelines |
| 4 | [MCP tools](../tutorials/04-mcp-tools/README.md) | Local and remote tools through one contract |
| 5 | [A2A agents](../tutorials/05-a2a-agents/README.md) | Local and remote agents through one contract |
| 6 | [Agent skills](../tutorials/06-agent-skills/README.md) | Reusable skills outside an agent's own instructions |
| 8 | [Telemetry](../tutorials/08-telemetry/README.md) | Native traces, live OTel, replay |
| 9 | [KG & SCOPE](../tutorials/09-kg-scope/README.md) | Knowledge graph from traces |
| 10 | [Evaluation metrics](../tutorials/10-evaluation-metrics/README.md) | Scores, judges, reusable metrics |
| 11 | [Sessions and recovery](../tutorials/11-sessions-and-recovery/README.md) | Checkpoint, fork, resume |
| 12 | [Spawned subagents](../tutorials/12-subagents/README.md) | Bounded subagent templates |
| 13 | [Control attach and debug](../tutorials/13-control-and-debug/README.md) | Interrupt, persist, resume, debug scripts |
| 14 | [Governance and HITL](../tutorials/14-governance-hitl/README.md) | Policy and human approval |

## Labs

| Family | Question | Where |
| --- | --- | --- |
| [IoC motivation](labs.md#ioc-motivation-lab) | How do multi-agent systems fail? | Labs index |
| [L9 evaluation](labs.md#l9-evaluation-labs) | Do ACCORD and CONCORD repair those failures, and at what cost? | Labs index |
| [Cognitive-challenge](labs.md#cognitive-challenge-labs) | Do the failures show up on other systems too? | Labs index |
| [Paper labs](../labs.md) | Is the method itself sound? | Toolkit labs page |
