<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Repo structure

<p class="eyebrow">A source checkout</p>

MAS Lab is a multi-package repository. Each top-level directory is one installable
piece, or the docs, tests, and labs that sit beside them.

```text
mas-lab/
├── runtime/               mas-runtime
├── ctl/                   mas-ctl
├── lab/                   mas-lab-core, mas-lab-bench, mas-lab-controller
├── library-standard/      flavours, overlays, infra bundles
├── library-samples/       reference apps and tutorial fixtures
├── library-eval/          evaluation metrics
├── library-lab/           shared lab/UI types
├── library-telemetry/     trace conversion and OpenTelemetry export
├── library-kg/            knowledge-graph pipelines
├── library-skills/        skill catalogue
├── library-ioa/           MCP bridge
├── labs/                  paper labs (*.lab/)
├── docs/                  this documentation site
├── ui/                    web UI (Yarn / Vite)
├── docker/                compose stack
├── tests/                 pytest
└── mkdocs.yml             site navigation
```

The Python packaging layout is generated into the
[packages reference](../packages-reference.md). Plugin entry points are in the
[plugins reference](../plugins-reference.md).

| If you are looking for | Go to |
| --- | --- |
| Agent / MAS YAML you can run | `library-samples/apps/` |
| Paper experiments | `labs/design-space.lab`, `labs/lifecycle-control.lab`, `labs/extensions.lab` |
| Tutorial walkthroughs | `docs/tutorials/` |
| CLI implementation | `ctl/`, `lab/` |
| Runtime kernel | `runtime/` |
| Web studio | `ui/` |

A **lab** folder (`*.lab/` plus `lab-config.yaml`) is not a library. A **library**
is a folder with `library.yaml`. See [labs vs libraries](../labs-and-libraries.md)
and [library discovery](../library-discovery.md).

## Lab, labs, and components

These words mean several things in a source checkout. Docs and code use one
vocabulary:

| Name | What it is |
| --- | --- |
| **package** | A published wheel (`mas-lab`, `mas-lab-core`, and so on). |
| **component** | One of the three sub-wheels under `lab/components/` (`mas-lab-core`, `mas-lab-bench`, `mas-lab-controller`). |
| **lab (definition)** | A `*.lab` folder under `labs/` that describes a paper experiment. |
| **runtime modules** | Code under `mas.lab.components` that the `mas-lab` package ships. Unrelated to the build components above. |
| **library** | A folder with `library.yaml` (`library-lab/` is `mas-library-lab`, a plugin library). |

Python namespaces keep `lab/components/*/src/mas/lab/...` (installed into
`mas.lab`) distinct from the `mas.lab.components` subpackage. The
[glossary](../glossary.md) uses the same words.
