<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Surfaces

<p class="eyebrow">How you talk to MAS-Lab</p>

| Surface | What it is for | Docs |
|---|---|---|
| **`mas-runtime`** | Kernel library. The console script only points at `mas-ctl`. | [mas-runtime](../cli/mas-runtime.md) |
| **`mas-ctl`** | Run, validate, compile and compose agents and MAS; chat; A2A `serve` | [mas-ctl](../cli/mas-ctl.md) |
| **`mas-lab`** | Workspace bootstrap, benchmarks, pipelines, telemetry, plots | [mas-lab](../cli/mas-lab.md) |
| **Terminal UI** | Interactive session in the terminal (`mas-ctl tui`) | [TUI](../ctl/tui.md) |
| **Web UI** | Browser studio: browse and validate manifests, launch benchmarks | [Web UI](../ui/index.md) |
| **Live dashboard** | Topology, events and chat for a running MAS or a finished run (`task demo-ui`, `mas-lab demo-ui`) | [Live dashboard](../ui/live.md) |
| **Controller API** | REST backend both web surfaces talk to (`mas-lab serve`) | [Controller API](../cli/serve.md) |

Index of every CLI and UI: [Command line](../cli/index.md).
Install and a first command: [Getting started](../getting-started.md).
Hands-on path: [Discover](../discover/index.md).
