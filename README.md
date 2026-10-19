<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->

# MAS-Lab

A **specification-driven foundation** for building multi-agent systems that are
testable, reproducible, observable, and governable from design to production.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

Modern multi-agent systems are easy to prototype, but hard to trust at scale.
MAS-Lab helps developers, enterprises, and researchers move from prompt-glued
prototypes to engineered agentic systems — with explicit specifications, runtime
contracts, reproducible experiments, and built-in observability.

- **Documentation site:** [outshift-open.github.io/mas-lab](https://outshift-open.github.io/mas-lab/)
- **Release process:** [RELEASE-PROCESS.md](RELEASE-PROCESS.md)
- **Release overview:** [blog post](docs/blog/posts/2026-06-17-v0-1-release/)

## Get started

<!-- TEMPORARY: remove this note after the first successful public PyPI upload. -->
> **PyPI publication pending:** the package names and installation path are
> prepared, but the public packages are not uploaded yet. Use the Docker or
> developer workspace paths below until the first PyPI release is complete.

```bash
# 1 — Install the published packages
uv venv
uv pip install mas-lab mas-library-standard mas-library-samples
export PATH="$PWD/.venv/bin:$PATH"

# 2 — Configure LLM access (interactive, writes ~/.config/mas/config.yaml)
mas-lab init

# 3 — Export the API key printed by init
export OPENAI_API_KEY=<your-key>

# 4 — Run the trip-planner sample
mas-ctl run-mas library-samples/apps/trip-planner/mas.yaml \
  --infra-ref standard:openai \
  -q "Plan a trip from Celestia to Verdantia"

# 5 — Inspect traces
mas-lab telemetry show library-samples/apps/trip-planner/traces/events.jsonl
mas-lab plot trajectory library-samples/apps/trip-planner/traces/events.jsonl \
  --format html -o traces/trip-planner-trajectory.html
```

Then continue with:

| Path              | Link                                                         |
| ----------------- | ------------------------------------------------------------ |
| Tutorials         | [docs/tutorials/](docs/tutorials/index.md)                   |
| Web UI (authoring)| [docs/ui/index.md](docs/ui/index.md)                         |
| Live dashboard    | [docs/ui/live.md](docs/ui/live.md)                           |
| Paper labs        | [docs/paper/index.md](docs/paper/index.md)                   |
| Labs vs libraries | [docs/labs-and-libraries.md](docs/labs-and-libraries.md)     |

Full install instructions: **[Tutorial 0 — Environment setup](docs/tutorials/00-environment-setup/README.md)**.
Full site content mirrors [`docs/`](docs/) — see [docs/index.md](docs/index.md) for the full introduction.

Choose the installation mode that matches the job:

| Mode | Use it for | Validation entry point |
| --- | --- | --- |
| **PyPI + uv venv** | Running a published release | `mas-ctl validate` / `mas-ctl chat` |
| **Docker** | Running without a local Python environment | `docker compose --profile tools run --rm cli mas-ctl validate ...` |
| **Developer workspace** | Editing MAS-Lab itself | `uv run pytest`, `task verify-unit`, or `task ci` |

## The problem

Prototype demos are easy; production-grade trust is not. Prompts, tools,
orchestration, and control are often interwoven, so behavior is hard to reproduce,
debug, or govern. MAS-Lab separates **intent**, **execution**, **observability**,
and **governance** through a shared specification and runtime model.

## What MAS-Lab provides

- Declarative MAS specifications (agents, tools, workflows, contracts)
- Runtime enforcement at system boundaries
- Governance and experimentation overlays
- Observability, replay, and benchmark pipelines
- Three reproducible [paper labs](docs/paper/index.md) (Section 5)

## Protocol integrations: optional runtime adapters, not core logic

MCP and A2A are not required to validate an agent's reasoning, workflow, or
logic. They are production deployment concerns: remote tool access and remote
agent communication.

That distinction is deliberate. A team can build and test an agent with local
contracts and local execution without any protocol wiring at all. Once the system
needs a remote tool server or a remote peer, MAS-Lab swaps in an infra adapter
instead of rewriting the agent logic.

This matters because protocol integration is not just “another plugin.” It
involves discovery, routing, transport compatibility, authentication, lifecycle
semantics, compliance behavior, and ongoing maintenance as the protocol evolves.
A good runtime treats this as a platform concern, not a business-logic concern.

With the right separation, the system gains several benefits:

- logic remains stable while transport and protocol choices change;
- teams can certify protocol behavior once and reuse it across many agents;
- new versions and compliance fixes land in the infra layer rather than in each agent;
- local development remains lightweight: minimal agents can avoid remote protocol code entirely;
- security, auditability, and code review happen at the integration boundary instead of scattered through agent logic.

This is the design underlying the MCP and A2A tutorials:

- [docs/tutorials/04-mcp-tools/README.md](docs/tutorials/04-mcp-tools/README.md) — switching local tool execution to MCP without rewriting agent logic
- [docs/tutorials/05-a2a-agents/README.md](docs/tutorials/05-a2a-agents/README.md) — switching a delegated agent path from local to A2A without changing the MAS workflow
- [docs/references/tool-server-registry.md](docs/references/tool-server-registry.md) — full MCP infra reference
- [docs/a2a/developer.md](docs/a2a/developer.md) — A2A contract and routing reference
- [docs/manifests/infra.md](docs/manifests/infra.md) — infrastructure manifest model and separation of concerns

## Who it is for

- **Developers** — specs instead of glue code; [tutorials](docs/tutorials/index.md)
- **Enterprises** — overlays for policy, audit, and control; [user guide](docs/user-guide.md)
- **Researchers** — reproducible campaigns; [paper labs](docs/paper/index.md)

## Packages

The headline packages:

| Package                | Role                                                           |
| ---------------------- | -------------------------------------------------------------- |
| `mas-runtime`          | Agent runtime — contracts, plugins, design patterns            |
| `mas-ctl`              | Orchestration — `chat`, `run-mas`, `validate` ([flags](docs/cli/mas-ctl.md)) |
| `mas-lab`              | Meta-package — benchmarks, pipelines, telemetry, UI controller |
| `mas-library-standard` | Flavours, overlays, infra bundles                              |

`mas-lab` is a meta-package that installs the lab components (`mas-lab-core`,
`mas-lab-bench`, and `mas-lab-controller`).
Additional libraries ship alongside it (`mas-library-eval`, `mas-library-lab`,
`mas-library-samples`).

A **lab** (`*.lab/` + `lab-config.yaml`) is the experiment surface. A
**library** is a folder with `library.yaml`. When to create each, how to
keep a local library inside a lab, and how `name:path` refs work:
[docs/labs-and-libraries.md](docs/labs-and-libraries.md). Discovery
contract (developers): [docs/library-discovery.md](docs/library-discovery.md).

See [docs/libraries.md](docs/libraries.md) for the installable package map and
[docs/packages-reference.md](docs/packages-reference.md) for the complete,
auto-generated package list with dependencies and extras.

## Supported versions

Security fixes are applied to the latest release on the `main` branch.

| Version               | Supported   |
| --------------------- | ----------- |
| latest on `main`      | yes         |
| older tagged releases | best effort |

## Citing this work

If you use MAS-Lab in research or publications, cite the
[MAS-Lab article](docs/paper/index.md) — not only this repository.

## Contributing

Contributions are what make the open source community such an amazing place to
learn, inspire, and create. Any contributions you make are **greatly
appreciated**. For detailed contributing guidelines, please see
[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md)

## License

Distributed under the `Apache 2.0` License. See [LICENSE](LICENSE) for more
information.
