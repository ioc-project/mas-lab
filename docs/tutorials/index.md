<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Lab — Hands-On Tutorials

These tutorials take you from one agent to full experiments. Each builds on the
previous ones. You can also [reproduce the paper labs](../paper/index.md) or use
the [web UI](../ui/index.md).

**Start with Tutorial 0.** It covers the Docker or developer install, LLM
credentials, and a check that everything works. The CLI and web UI share it.

## Choose a route

| If you want to... | Start here |
| --- | --- |
| See a multi-agent system run | [Tutorial 0](00-environment-setup/README.md), then [Tutorial 1](01-building-an-agent/README.md) |
| Understand the model: agents, MAS, overlays | Tutorials [0](00-environment-setup/README.md), [1](01-building-an-agent/README.md), [2](02-creating-a-mas/README.md) |
| Run a controlled experiment | [Tutorial 3](03-experiments-and-analysis/README.md), then the [paper labs](../labs.md) |
| Add governance and human approval | [Tutorial 14](14-governance-hitl/README.md) |
| Export traces to an observability stack or a knowledge graph | Tutorials [8](08-telemetry/README.md) and [9](09-kg-oxp/README.md) |
| Click instead of type | The [web UI](../ui/index.md) |

| # | Tutorial | What you learn |
| --- | --- | --- |
| 0 | [Environment Setup](00-environment-setup/README.md) | Install, PATH, model endpoint, API key wiring |
| 1 | [Build an agent](01-building-an-agent/README.md) | Agent manifest, tools, skills, memory, CLI, traces |
| 2 | [Orchestrate your MAS](02-creating-a-mas/README.md) | MAS manifest, delegation, topology overlays |
| 3 | [Run an experiment](03-experiments-and-analysis/README.md) | Experiments, benchmarks, pipelines, and MCE evaluation (Metrics Computation Engine) |
| 4 | [MCP tools](04-mcp-tools/README.md) | Call local and remote tools (Model Context Protocol) through one contract |
| 5 | [A2A agents](05-a2a-agents/README.md) | Call local and remote agents (Agent2Agent) through one contract |
| 6 | [Agent skills](06-agent-skills/README.md) | Give an agent a reusable skill (procedure, reference material, checked script) outside its own instructions |
| 7 | *(reserved)* | |
| 8 | [Telemetry](08-telemetry/README.md) | Native traces, live OTel, replay, collector / ClickHouse |
| 9 | [KG & OXP](09-kg-oxp/README.md) | Build a knowledge graph (KG) from native traces or OpenTelemetry (OTel) spans, using the Observe and eXplain Platform (OXP) `norm` normalizer; write to Neo4j |
| 10 | [Evaluation metrics](10-evaluation-metrics/README.md) | Stock MCE, one-off prompts, reusable EvalMetrics, judge infra |
| 11 | [Sessions and recovery](11-sessions-and-recovery/README.md) | Checkpoint, fork, resume and recover a conversation |
| 12 | [Spawned subagents](12-subagents/README.md) | Run bounded, pre-authored subagent templates during a turn |
| 13 | [Control attach and debug scripts](13-control-and-debug/README.md) | Interrupt a bad session, persist, resume; investigate; steer-to-fix on the restarted chat; auto-checkpoint tree; gdb `script_file` / `--debug-script` |
| 14 | [Governance and HITL](14-governance-hitl/README.md) | Require human or policy approval (human in the loop, HITL) before a tool call runs |

After Tutorial 14, reproduce the paper results in three labs: [Paper](../paper/index.md).

Do Tutorials 0 to 3 in order: install, then one agent, then a MAS, then
experiments. Tutorials 4 to 6 and 8 to 14 each cover one capability. Each page
lists its prerequisites at the top, usually Tutorial 0 plus the agent or MAS it
reuses.

Every tutorial except 10 ships a `demo/scenario.yaml` with steps, commands,
expected exit codes, and sometimes expected output. CI runs the offline steps
with `tests/tutorials/test_scenario_commands.py`, so the commands stay working.

To replay the offline commands, run this from the repository root:

```bash
pytest tests/tutorials/test_scenario_commands.py -v              # offline steps only
pytest tests/tutorials/test_scenario_commands.py -v -k tuto-03   # one tutorial
TUTORIAL_ONLINE=1 pytest tests/tutorials/test_scenario_commands.py -v -k tuto-01   # also live-LLM steps
```

Telemetry is Tutorial 8 (`mas-library-telemetry`). Knowledge-graph
normalization is Tutorial 9 (`mas-library-kg`).

## Sample applications

The labs run on sample systems from `library-samples/apps`. They are also the
fastest way to see the runtime work.

| Application | What it is | Used by |
| --- | --- | --- |
| `trip-planner` | A four-agent moderator and specialists system | Topology and memory experiments |
| `qa-mas` | A one-agent MAS wrapping a question-answering agent | Design-pattern experiments |

Both come with datasets. The trip planner's is `trip-planner-benchmark`.

## What's next

After Tutorial 14:

- **Labs:** runnable experiments live in [`labs/`](https://github.com/outshift-open/mas-lab/tree/main/labs): `design-space.lab` (design patterns and topologies), `lifecycle-control.lab`, and `extensions.lab`. See [Labs](../labs.md) and the [paper index](../paper/index.md). Lab vs library: [labs-and-libraries.md](../labs-and-libraries.md).
- **Protocol references:** the [MCP ToolServerRegistry reference](../references/tool-server-registry.md) and the [A2A developer reference](../a2a/developer.md).

Next: [Tutorial 0 — Environment setup](00-environment-setup/README.md).
