<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# What is MAS-Lab?

<p class="eyebrow">The toolkit</p>

MAS Lab builds, runs, and tests multi-agent systems from YAML specifications.

A multi-agent system (MAS) is a team of LLM agents with tools and a workflow
that says who talks to whom. MAS Lab is an open toolkit to engineer one.

1. You describe agents, tools, workflows, and policies in YAML files (manifests).
2. The runtime runs them and checks behaviour at boundaries such as tool calls and delegations.
3. Experiments and traces let you compare designs and audit what happened.

The same specification serves local development, benchmarks, and governed
deployment.

This page is the toolkit view. For MAS-Lab's place in the Internet of Cognition,
start at the [landing page](../index.md).

## Why use it

Prototype demos are easy. Production systems are hard to reproduce, debug, and
govern. A small change to a prompt, a tool, or the coordination can silently
change outcomes.

MAS Lab separates what the system should do (the spec) from what it did (the
trace). Contracts, overlays, and observability connect the two.

## Who it is for

- **Developers** build modular agent apps from specs. Declare agents, tools, and workflows once, then try other models and coordination patterns.
- **Enterprises** add governance, traceability, and budgets as overlays. An overlay is a small YAML file merged onto a manifest, so core logic is untouched.
- **Researchers** run controlled multi-run experiments. They vary designs and infrastructure, and replay recorded runs.

More on the problem and the design: [The problem](../overview.md).

## How it is built

The rest of Getting started splits the former architecture page into shorter
topics. The five stages, the experiment control plane, the packages and the
repository layout stay in this sidebar.

| Topic | What it covers |
| --- | --- |
| [The problem](../overview.md) | Why mixed prompts and glue code fail, who the toolkit is for |
| [Architecture](../architecture.md) | Five stages from declaration to result |
| [Experiments](experiments.md) | Control plane, design modes, where guarantees come from |
| [Packages](packages.md) | Installable Python packages and how they layer |
| [Repo structure](repo.md) | Directories in a source checkout |
| [Surfaces](surfaces.md) | `mas-ctl`, `mas-lab`, web UI, live dashboard, controller API |

## Where to start

- [Getting started](../getting-started.md): install and run a first command.
- [Discover](../discover/index.md): tutorials, labs, and IoC integrations.
- [User guide](../user-guide.md): day-to-day use.

Browse manifests and runs in a browser with the [Web UI](../ui/index.md). Watch
runs live in the [live dashboard](../ui/live.md). For what shipped in this
release, read the [v0.2 release post](../blog/posts/2026-09-30-v0-2-release/index.md).

## Reference pages

| Topic | Where |
| --- | --- |
| Install and day-to-day use | [User guide](../user-guide.md) |
| Command line | [CLI index](../cli/index.md) · [mas-ctl](../cli/mas-ctl.md) · [mas-lab](../cli/mas-lab.md) |
| Workspace `config.yaml` | [config.yaml reference](../references/config.yaml.md) |
| Labs vs libraries | [labs-and-libraries.md](../labs-and-libraries.md) |
| Terms | [Glossary](../glossary.md) |
| OpenAI, MCP, A2A, Agent Skills, OpenTelemetry | [Open standards](../standards/index.md) |
| YAML manifests, schemas, contracts | [References](../references/index.md) |
| Design-space exploration labs | [Paper](../paper/index.md) |
| Releases and updates | [Blog](../blog/index.md) |

## Citing this work

If you use MAS Lab in research or publications, cite the
[MAS Lab article](https://arxiv.org/abs/2606.30546)
([arXiv:2606.30546](https://arxiv.org/abs/2606.30546)), not only this
repository. BibTeX and reproduction details are on the
[Paper](../paper/index.md) page.

Next: [Install](../getting-started.md) or
[Architecture](../architecture.md).
