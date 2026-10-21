<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# References

This hub lists the reference pages for MAS Lab: YAML manifests, schemas, runtime contracts, and lab benchmarking. Unsure about a term? Check the [glossary](../glossary.md).

---

## Specifications

The YAML kinds and how they compose.

| Topic | Reference |
|-------|-----------|
| Overview & composition | [Manifest overview](../manifests/README.md) |
| Agent | [agent.md](../manifests/agent.md) · [governance.md](../manifests/governance.md) (`spec.governance`) |
| MAS & workflow | [mas.md](../manifests/mas.md) |
| Overlay | [overlay.md](../manifests/overlay.md) |
| Flavour & environment | [flavour.md](../manifests/flavour.md), [infra.md](../manifests/infra.md) |
| Workspace file | [config.yaml reference](config.yaml.md), [user-config.md](../user-config.md), [config.schema.yaml](../schemas/config.schema.yaml) |
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
| Contracts & Mealy envelope | [Contracts](contracts.md) · [runtime package docs](runtime.md) |
| Thin waist (plugin vs catalog) | [thin-waist.md](thin-waist.md) |
| Snapshots vs checkpoints | [snapshots.md](snapshots.md) |
| Kernel operations | [kernel-primitives.md](kernel-primitives.md) |
| CLI (`mas-ctl`, `mas-runtime`) | [mas-ctl reference](../cli/mas-ctl.md) · [ctl user guide](https://github.com/outshift-open/mas-lab/blob/main/ctl/docs/user-guide.md) |
| **Web UI** | [ui/index.md](../ui/index.md) |
| Run logs | [Observability](../cli/observability.md) |

---

## Lab & benchmarks

Experiments, datasets, pipelines, and analysis (`mas-lab`).

| Topic | Reference |
|-------|-----------|
| Experiment manifest | [experiment.md](../manifests/experiment.md) |
| Dataset | [dataset.md](../manifests/dataset.md) |
| Pipeline steps | [pipeline.md](../manifests/pipeline.md) |
| Interactive lab demo | [lab.md](../manifests/lab.md) |
| Bench design & steps | [Lab package docs](lab.md) |
| Package map | [libraries.md](../libraries.md) |

---

## Design decisions

- [ADR 0001 — lab terminology](adr-0001-lab-terminology.md)
- [ADR 0002 — observability event model](adr-0002-observability-event-model.md)

---

## See also

- [User guide](../user-guide.md): install and day-to-day workflows.
- [Tutorials](../tutorials/index.md): hands-on, from agents to teams to experiments.
- [Paper labs](../paper/index.md): reproduce Section 5 of the article.
- [Web UI](../ui/index.md): design and inspect in the browser.
