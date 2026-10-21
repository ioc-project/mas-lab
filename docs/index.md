<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Lab

MAS Lab builds, runs, and tests multi-agent systems from YAML specifications.

<div class="mas-home" markdown="1">

## What it is

A multi-agent system (MAS) is a team of LLM agents with tools and a workflow
that says who talks to whom. MAS Lab is an open toolkit to engineer one.

1. You describe agents, tools, workflows, and policies in YAML files (manifests).
2. The runtime runs them and checks behaviour at boundaries such as tool calls and delegations.
3. Experiments and traces let you compare designs and audit what happened.

The same specification serves local development, benchmarks, and governed
deployment.

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

More on the problem and the design: [overview](overview.md).

## Where to start

- [Quickstart](tutorials/00-environment-setup/README.md): install and run a first command.
- [Tutorials](tutorials/index.md): from one agent to a governed experiment.
- [Labs](labs.md): reproduce the paper experiments.
- [Architecture](architecture.md): the five stages and the packages.

Browse manifests and runs in a browser with the [Web UI](ui/index.md). Watch runs
live in the [live dashboard](ui/live.md). For what shipped in this release, read the
[v0.2 release post](blog/posts/2026-09-30-v0-2-release/index.md).

## Reference pages

| Topic | Where |
| --- | --- |
| Install and day-to-day use | [User guide](user-guide.md) |
| Command line | [CLI](cli/index.md) · [mas-ctl options](cli/mas-ctl.md) |
| Workspace `config.yaml` | [config.yaml reference](references/config.yaml.md) |
| Labs vs libraries | [labs-and-libraries.md](labs-and-libraries.md) |
| Terms | [Glossary](glossary.md) |
| YAML manifests, schemas, contracts | [References](references/index.md) |
| Design-space exploration labs | [Paper](paper/index.md) |
| Releases and updates | [Blog](blog/index.md) |

## Citing this work

If you use MAS Lab in research or publications, cite the
[MAS Lab article](paper/index.md), not only this repository. BibTeX and
reproduction details are on the Paper page.

Next: [Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md).

</div>
