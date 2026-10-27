<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# References

This hub lists the reference pages for MAS Lab: YAML manifests, schemas, runtime
contracts, and lab benchmarking. Unsure about a term? Check the
[glossary](../glossary.md). Open protocols (OpenAI, MCP, A2A, Agent Skills,
OpenTelemetry) live under [Open standards](../standards/index.md), not here.

Day-to-day topics live under [User guide](../user-guide.md):
[command-line index](../cli/index.md), [Web UI](../ui/index.md),
[live dashboard](../ui/live.md).

---

## CLI

Console scripts, flags, and the controller HTTP API.

| Topic | Reference |
|-------|-----------|
| `mas-runtime` | [mas-runtime.md](../cli/mas-runtime.md) |
| `mas-ctl` | [mas-ctl.md](../cli/mas-ctl.md) · [compile](../cli/compile.md) · [overrides](../cli/overrides.md) |
| `mas-lab` | [mas-lab.md](../cli/mas-lab.md) |
| Run logs | [Observability](../cli/observability.md) |
| Controller HTTP API | [Controller API](../cli/serve.md) |
| Live dashboard internals | [live-reference.md](../ui/live-reference.md) |

How the surfaces fit together: [command-line index](../cli/index.md) (User guide).

---

## Specifications

The YAML kinds and how they compose.

| Topic | Reference |
|-------|-----------|
| Overview & composition | [Manifest overview](../manifests/README.md) |
| Agent | [agent.md](../manifests/agent.md) · [governance.md](../manifests/governance.md) (`spec.governance`) · [checkpoints](../manifests/checkpoint.md) · [subagents](../manifests/subagents.md) |
| MAS & workflow | [mas.md](../manifests/mas.md) |
| Overlay | [overlay.md](../manifests/overlay.md) |
| Flavour & environment | [flavour.md](../manifests/flavour.md), [infra.md](../manifests/infra.md) |
| Workspace file | [config.yaml](config.yaml.md), [user-config.md](../user-config.md) |
| Compiled defaults | [What a minimal agent expands to](defaults.md) |
| Tools | [ToolContract](tool-contract.md) · [ToolServerRegistry](tool-server-registry.md) · [System tools](system-tools.md) |
| Schemas (all) | [Schema index](schemas.md) |
| Contracts | [Contracts](contracts.md) |

---

## Runtime

The execution kernel, contracts, and plugins (`mas-runtime`, `mas-ctl`).

| Topic | Reference |
|-------|-----------|
| Runtime manifests (Agent, MAS, overlay) | [Manifest fields](../manifests/runtime.md) |
| Context window, summarization, pinned prompt parts, cache and token usage | [Context management](context-management.md) |
| Reliability (retries, circuit, `error_policy`, logging) | [reliability.md](reliability.md) |
| LLM response cache (`llm_cache` infra middleware) | [llm-cache.md](llm-cache.md) |
| Communication headers | [comm-headers.md](comm-headers.md) |
| Contracts & Mealy envelope | [Contracts](contracts.md) · [runtime package docs](runtime.md) |
| Thin waist (plugin vs catalog) | [thin-waist.md](thin-waist.md) |
| Snapshots vs checkpoints | [snapshots.md](snapshots.md) |
| Kernel operations | [kernel-primitives.md](kernel-primitives.md) |
| Run logs | [Observability](../cli/observability.md) (CLI) |
| Native event stream | [Event model](event-model.md) |

---

## Lab

Experiments, datasets, pipelines, and analysis (`mas-lab`).

| Topic | Reference |
|-------|-----------|
| Experiment manifest | [experiment.md](../manifests/experiment.md) |
| Dataset | [dataset.md](../manifests/dataset.md) |
| Pipeline steps | [pipeline.md](../manifests/pipeline.md) |
| Interactive lab demo | [lab.md](../manifests/lab.md) |
| Bench design & steps | [Lab package docs](lab.md) |
| Library discovery | [library-discovery.md](../library-discovery.md) |

---

## Packages

| Topic | Reference |
|-------|-----------|
| Package map | [libraries.md](../libraries.md) |
| Generated package list | [packages-reference.md](../packages-reference.md) |
| Generated plugin list | [plugins-reference.md](../plugins-reference.md) |

---

## See also

- [Getting started](../getting-started.md): install.
- [User guide](../user-guide.md): day-to-day workflows.
- [Open standards](../standards/index.md): OpenAI, MCP, A2A, Agent Skills, OpenTelemetry.
- [Tutorials](../tutorials/index.md): hands-on, from agents to teams to experiments.
- [Paper labs](../paper/index.md): reproduce Section 5 of the article.
