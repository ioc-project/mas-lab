<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Packages

<p class="eyebrow">What you install</p>

Each layer depends only on the layers above it in this table. There are no cycles.

| Layer | Package | Role |
|---|---|---|
| **Runtime** | `mas-runtime` | The kernel, contracts, plugins and design patterns. Library only; the console script points at `mas-ctl` |
| **Orchestration** | `mas-ctl` | Builds and runs sessions: `chat`, `run-mas`, `validate`, `compile`, `compose` |
| **Lab** | `mas-lab-core` | Shared contracts and telemetry types |
| | `mas-lab-bench` | Runs benchmarks and analysis pipelines |
| | `mas-lab-controller` | A background service with an HTTP API, used by the web UI |
| | `mas-lab` | Installs the three above and provides the `mas-lab` command |
| **Libraries** | `mas-library-standard` | Flavours, overlays, infra bundles, built-in plugins |
| | `mas-library-eval` | Evaluation metrics consumed by pipeline steps |
| | `mas-library-lab` | Shared types and backend code for the lab and its UI |
| | `mas-library-samples` | Reference applications, datasets, tutorial fixtures |
| | `mas-library-telemetry` | Converts and replays traces, exports to OpenTelemetry |
| | `mas-library-kg` | Turns traces into a knowledge graph |
| | `mas-library-skills` | The skill catalogue and the tools that read skills |
| | `mas-library-ioa` | MCP client/server bridge |

A *lab* is an experiment folder. A *library* is a folder with a `library.yaml`
holding reusable applications, tools and plugins, referenced as `name:path`. The
two are deliberately different things.

See the [package map](../libraries.md), the [packages reference](../packages-reference.md)
and [labs vs libraries](../labs-and-libraries.md). Directories in a checkout are
on [Repo structure](repo.md).
