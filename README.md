<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->

# MAS Lab

MAS Lab builds, runs, and tests multi-agent systems (MAS) from YAML files. A MAS
is a team of LLM agents with tools and a workflow that says who talks to whom.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)

It is for three audiences:

- **Developers** write agents, tools, and workflows as specs instead of glue code. Start with the [tutorials](docs/tutorials/index.md).
- **Enterprises** add policy, audit, and budget controls as overlays. Start with the [user guide](docs/user-guide.md).
- **Researchers** run repeatable experiments and compare designs. Start with the [paper labs](docs/paper/index.md).

Links:

- **Documentation site:** [ioc-project.github.io/mas-lab](https://ioc-project.github.io/mas-lab/)
- **Release process:** [RELEASE-PROCESS.md](RELEASE-PROCESS.md)
- **Release notes:** [v0.1 post](docs/blog/posts/2026-06-17-v0-1-release/index.md) · [v0.2 post](docs/blog/posts/2026-09-30-v0-2-release/index.md)

## What MAS Lab provides

Prototype multi-agent demos are easy. Trusting them in production is not,
because prompts, tools, orchestration, and control are interwoven. Behaviour is
then hard to reproduce, debug, or govern.

MAS Lab separates intent, execution, observability, and governance through one
specification. It provides:

- Declarative specs for agents, tools, workflows, and contracts.
- Runtime enforcement of those contracts at system boundaries.
- Governance and experiment overlays (small YAML files merged onto a spec).
- Tracing, replay, and benchmark pipelines.
- Three reproducible [paper labs](docs/paper/index.md) (Section 5 of the article).

## Get started

<!-- TEMPORARY: remove this note after the first successful public PyPI upload. -->
> **PyPI publication pending:** the package names and installation path are
> prepared, but the public packages are not uploaded yet. Use the Docker or
> developer workspace paths below until the first PyPI release is complete.

```bash
# 1 — Install the packages (once published; until then see the paths below)
uv venv
uv pip install mas-lab mas-library-standard mas-library-samples
export PATH="$PWD/.venv/bin:$PATH"

# 2 — Configure LLM access (interactive, writes ~/.config/mas/config.yaml)
mas-lab init

# 3 — Export the API key printed by init
export OPENAI_API_KEY=<your-key>

# 4 — Run the trip-planner sample
#    (`samples:` resolves the app shipped in mas-library-samples; from a source
#    checkout you can pass library-samples/apps/trip-planner/mas.yaml instead)
mas-ctl run-mas samples:apps/trip-planner/mas.yaml \
  --infra-ref standard:openai \
  --events-file "$PWD/traces/trip-planner.jsonl" \
  -q "Plan a trip from Celestia to Verdantia"

# 5 — Inspect the trace written by step 4
mas-lab telemetry show traces/trip-planner.jsonl
mas-lab plot trajectory traces/trip-planner.jsonl \
  --format html -o traces/trip-planner-trajectory.html
```

Step 4 writes `traces/trip-planner.jsonl`, the machine-readable trace of the
run (one JSON event per line). Step 5 prints it and plots the trajectory.

Pick the installation mode that matches your job:

| Mode | Use it for | Validation entry point |
| --- | --- | --- |
| **PyPI + uv venv** (once published) | Running a published release | `mas-ctl validate` / `mas-ctl chat` |
| **Docker** | Running without a local Python environment | `docker compose --profile tools run --rm cli mas-ctl validate ...` |
| **Developer workspace** | Editing MAS Lab itself | `uv run pytest`, `task verify-unit`, or `task ci` |

Full install steps: [Tutorial 0 — Environment setup](docs/tutorials/00-environment-setup/README.md).
The documentation site mirrors [`docs/`](docs/); start at [docs/index.md](docs/index.md).

Then continue with:

| Path              | Link                                                         |
| ----------------- | ------------------------------------------------------------ |
| CLI (`mas-lab`)   | [docs/cli/mas-lab.md](docs/cli/mas-lab.md)                   |
| CLI (`mas-ctl`)   | [docs/cli/mas-ctl.md](docs/cli/mas-ctl.md)                   |
| Tutorials         | [docs/tutorials/](docs/tutorials/index.md)                   |
| Web UI (authoring)| [docs/ui/index.md](docs/ui/index.md)                         |
| Live dashboard    | [docs/ui/live.md](docs/ui/live.md)                           |
| Paper labs        | [docs/paper/index.md](docs/paper/index.md)                   |
| Labs vs libraries | [docs/labs-and-libraries.md](docs/labs-and-libraries.md)     |

## Protocols are adapters, not core logic

MCP (Model Context Protocol, for remote tools) and A2A (Agent2Agent, for remote
agents) are deployment concerns. You do not need them to test an agent's
reasoning or workflow.

Build and test an agent with local tools and local delegation first. When you
need a remote tool server or a remote peer, MAS Lab swaps in an infra adapter.
The agent logic does not change.

Keeping protocols in the infra layer has these effects:

- Agent logic stays stable while transport and protocol choices change.
- You certify protocol behaviour once and reuse it across agents.
- Protocol upgrades and compliance fixes land in one place, not in every agent.
- Minimal agents carry no remote-protocol code.
- Security review and auditing happen at the integration boundary.

Details:

- [Tutorial 4](docs/tutorials/04-mcp-tools/README.md) switches local tool execution to MCP.
- [Tutorial 5](docs/tutorials/05-a2a-agents/README.md) switches a delegated agent from local to A2A.
- [MCP infra reference](docs/references/tool-server-registry.md)
- [A2A](docs/standards/a2a.md) · [A2A internals](docs/a2a/developer.md)
- [Infra manifest model](docs/manifests/infra.md)

## Packages

| Package                | Role                                                           |
| ---------------------- | -------------------------------------------------------------- |
| `mas-runtime`          | Agent runtime — contracts, plugins, design patterns            |
| `mas-ctl`              | Orchestration — `chat`, `run-mas`, `validate` ([flags](docs/cli/mas-ctl.md)) |
| `mas-lab`              | Meta-package — benchmarks, pipelines, telemetry, UI controller |
| `mas-library-standard` | Flavours, overlays, infra bundles                              |

`mas-lab` installs three lab components (`mas-lab-core`, `mas-lab-bench`,
`mas-lab-controller`) plus `mas-runtime`, `mas-ctl`, and `mas-library-eval`.
Add other libraries as separate packages: `mas-library-samples`,
`mas-library-lab`, `mas-library-ioa`, `mas-library-kg`, `mas-library-skills`,
`mas-library-telemetry`.

A **lab** (`*.lab/` plus `lab-config.yaml`) is where you run experiments. A
**library** is a folder with `library.yaml` holding reusable apps, tools, and
plugins. See [labs and libraries](docs/labs-and-libraries.md) for when to create
each and how `name:path` references work. Developers: see the
[library discovery contract](docs/library-discovery.md).

The [package map](docs/libraries.md) lists installable packages. The
[packages reference](docs/packages-reference.md) is auto-generated and lists
dependencies and extras.

## Supported versions

Security fixes are applied to the latest release on the `main` branch.

| Version               | Supported   |
| --------------------- | ----------- |
| latest on `main`      | yes         |
| older tagged releases | best effort |

## Citing this work

If you use MAS Lab in research or publications, cite the
[MAS Lab article](https://arxiv.org/abs/2606.30546)
([arXiv:2606.30546](https://arxiv.org/abs/2606.30546)) — not only this
repository. Reproduction of Section 5 is on the
[Paper](docs/paper/index.md) page.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).

## License

Distributed under the `Apache 2.0` License. See [LICENSE](LICENSE) for more
information.
