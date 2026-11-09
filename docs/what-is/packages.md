<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Packages

<p class="eyebrow">What you install</p>

Each layer depends only on the layers above it. There are no cycles.

| Layer | Directory | Role |
|---|---|---|
| **Runtime** | `runtime/` | The kernel, contracts, plugins and design patterns. Library only; the console script points at `mas-ctl` |
| **Orchestration** | `ctl/` | Builds and runs sessions: `chat`, `run-mas`, `validate`, `compile`, `compose` |
| **Lab** | `lab/components/core/` | Shared contracts, telemetry types, and the `mas-lab` command |
| | `lab/components/bench/` | Runs benchmarks and analysis pipelines |
| | `lab/components/controller/` | A background service with an HTTP API, used by the web UI |
| | `.` (repo root) | Empty meta-package that installs the rest of the stack |
| **Libraries** | `library-standard/` | Flavours, overlays, infra bundles, built-in plugins |
| | `library-eval/` | Evaluation metrics consumed by pipeline steps |
| | `library-lab/` | Shared types and backend code for the lab and its UI |
| | `library-samples/` | Reference applications, datasets, tutorial fixtures |
| | `library-telemetry/` | Converts and replays traces, exports to OpenTelemetry |
| | `library-kg/` | Turns traces into a knowledge graph |
| | `library-skills/` | The skill catalogue and the tools that read skills |
| | `library-ioa/` | MCP client/server bridge, A2A plugins, and SLIM, HTTP and A2A framing stack layers |

A *lab* is an experiment folder. A *library* is a folder with a `library.yaml`
holding reusable applications, tools and plugins, referenced as `name:path`. The
two are deliberately different things.

PyPI wheel names are generated from each directory's `pyproject.toml` into the
[packages reference](../packages-reference.md). Plugin entry points are in the
[plugins reference](../plugins-reference.md). Directories in a checkout are
on [Repo structure](repo.md). See also the [package map](../libraries.md)
and [labs vs libraries](../labs-and-libraries.md).
