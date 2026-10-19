<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS-Lab

**A specification-driven foundation** for multi-agent systems that are testable,
reproducible, observable, and governable.

Multi-agent demos are easy to build; production-grade systems are not. MAS-Lab
helps teams close that gap with declarative specs, runtime enforcement, and
repeatable experiments — without gluing everything together in prompts and scripts.

<div class="mas-home" markdown="1">

## What it is

MAS-Lab is an open toolkit for **engineering** multi-agent systems. You describe
agents, teams, tools, workflows, and policies in YAML; the runtime executes and
checks behavior at important boundaries; experiments and traces let you compare
designs and audit what happened.

The same specification travels from local development through benchmarks to
governed deployment — intent stays explicit instead of scattered across code and
prompts.

## Why it exists

As systems grow, behavior becomes hard to reproduce, debug, and govern. Small
changes to prompts, tools, or coordination can silently shift outcomes. MAS-Lab
separates **what the system should do** from **what it actually did at runtime**,
and connects them with specs, contracts, overlays, and observability.

## Who it is for

**Developers** — build modular agent apps from specs instead of one-off glue.
Declare agents, tools, and workflows once, then iterate safely across models and
coordination patterns.

**Enterprises** — roll out agentic workflows with governance, traceability, and
operational control. Policies and budgets attach as overlays without rewriting
core logic.

**Researchers** — run controlled, multi-run campaigns; vary designs and
infrastructure; replay trajectories with reproducibility built in.

More on each audience in the [overview](overview.md).

## Go deeper

| Topic | Where |
| --- | --- |
| Install and day-to-day use | [User guide](user-guide.md) |
| Command line | [CLI](cli/index.md) · [mas-ctl options](cli/mas-ctl.md) |
| Workspace `config.yaml` | [config.yaml reference](references/config.yaml.md) |
| Labs vs libraries | [labs-and-libraries.md](labs-and-libraries.md) |
| Hands-on path (agent → team → experiment) | [Tutorials](tutorials/index.md) |
| Live telemetry dashboard | [Live dashboard](ui/live.md) |
| YAML manifests, schemas, contracts | [References](references/index.md) |
| Design-space exploration labs | [Paper](paper/index.md) |
| Releases and updates | [Blog](blog/index.md) |

## Where to start

If you are new to MAS-Lab, begin with
[Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md) to
install the stack and run your first commands. The [Web UI](ui/index.md) lets you
browse manifests and inspect runs in the browser. The [live dashboard](ui/live.md) is a
separate surface over `/ws` and `/api/ingest`. For context on what shipped in
this release, read the
[v0.2 release post](blog/posts/2026-09-30-v0-2-release/). For the research motivation,
formal model, and reproducible Section 5 labs, see the
[MAS-Lab paper](paper/index.md).

## Citing this work

If you use MAS-Lab in research or publications, please cite the
[MAS-Lab article](paper/index.md) — not only this repository. BibTeX and
reproduction details are on the [Paper](paper/index.md) page.

</div>
