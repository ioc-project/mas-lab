<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Lab packages

This page lists the installable Python packages of MAS Lab and what each does.
Most users call `mas-ctl` and `mas-lab`. `mas-runtime` is the kernel library
(its console script only points at `mas-ctl`). Extension authors add plugins,
pipeline steps, or libraries on top of the same contracts. Surfaces:
[command-line index](cli/index.md).

Install every package from a repository checkout:

```bash
uv sync --all-packages
```

Requires **Python 3.11+**.

---

## How the pieces fit together

| Layer | Package | What it does |
|-------|---------|--------------|
| **Runtime** | `mas-runtime` | Executes agents and teams from YAML manifests — contracts, plugins, traces |
| **Control plane** | `mas-ctl` | Chat, validate, compile, compose, and run multi-agent workflows (`run-mas`) |
| **Lab** | `mas-lab` | Benchmark experiments, pipelines, reports, and the web UI controller |
| **Standard library** | `mas-library-standard` | Flavours, overlays, infra bundles, and built-in runtime plugins |
| **Samples** | `mas-library-samples` | Reference apps (e.g. trip planner), datasets, and tutorial fixtures |
| **Optional libraries** | `mas-library-skills`, `mas-library-ioa`, `mas-library-kg`, `mas-library-telemetry` | Agent skills, MCP/A2A, trace-to-knowledge-graph pipelines, OpenTelemetry conversion and export |

A **lab** is a folder of experiments (`*.lab/` plus `lab-config.yaml`). A
**library** is a folder with `library.yaml`. See
[labs-and-libraries.md](labs-and-libraries.md) for when to create each.

**Typical path:** [Tutorial 0](tutorials/00-environment-setup/README.md) →
`mas-ctl` for agents and teams → `mas-lab benchmark run` for experiments.

Supporting packages are `mas-library-eval` and the three `mas-lab` components
(`mas-lab-core`, `mas-lab-bench`, `mas-lab-controller`). They provide shared
types, evaluation metrics, and web UI backend code. `mas-lab` installs them. You
rarely import them unless you extend the toolkit. `mas-library-lab` (lab
extension plugins and standard plots) is separate. Add it when you need it.

---

## Package reference

| Package | Directory | CLI | Role |
|---------|-----------|-----|------|
| `mas-runtime` | `runtime/` | `mas-runtime` | Headless agent execution (CI, containers) |
| `mas-ctl` | `ctl/` | `mas-ctl` | Interactive and scripted orchestration |
| `mas-lab` | `lab/` | `mas-lab` | Benchmarks, pipelines, controller API |
| `mas-library-standard` | `library-standard/` | — | Standard plugins, flavours, observability sinks |
| `mas-library-samples` | `library-samples/` | — | Sample MAS apps and benchmark datasets |
| `mas-library-lab` | `library-lab/` | — | Pipeline-step and artifact-codec plugins, eval provider, standard plots |
| `mas-library-eval` | `library-eval/` | — | Session-level evaluation metrics for benchmarks |
| `mas-library-skills` | `library-skills/` | — | Agent skills (progressive disclosure) |
| `mas-library-ioa` | `library-ioa/` | — | MCP client/server bridge and A2A plugins |
| `mas-library-kg` | `library-kg/` | — | Trace-to-KG normalization and KG pipelines |
| `mas-library-telemetry` | `library-telemetry/` | — | Native events to OpenTelemetry conversion and OTLP export |

The PyPI upload is still pending (see the
[README](https://github.com/ioc-project/mas-lab#get-started)). Until then,
install from a checkout. After the upload, `uv pip install mas-lab` pulls the
runtime, control plane, and lab stack. The generated list with dependencies and
extras is in [packages-reference.md](packages-reference.md).

---

## Where to read more

| Goal | Start here |
|------|------------|
| First install | [Tutorial 0](tutorials/00-environment-setup/README.md) |
| Day-to-day use | [User guide](user-guide.md) |
| Lab vs library | [labs-and-libraries.md](labs-and-libraries.md) |
| YAML manifests | [References](references/index.md) |
| Paper reproduction | [Paper labs](paper/index.md) |
| Library discovery | [library-discovery.md](library-discovery.md) |
| Runtime development | [`runtime/docs/`](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/index.md) in the repository |
| Control plane | [`ctl/docs/`](https://github.com/ioc-project/mas-lab/blob/main/ctl/docs/user-guide.md) in the repository |

Site: [ioc-project.github.io/mas-lab](https://ioc-project.github.io/mas-lab/)

---

## Schemas

Workspace configuration: [config.schema.yaml](schemas/config.schema.yaml).

Experiment and benchmark manifests are validated by `mas-lab validate` against
the schemas under [`docs/schemas/lab/`](schemas/lab/experiment.schema.yaml).
Manifest field reference: [References](references/index.md).

---

## Extending MAS Lab

| Area | Extend | Entry points |
|------|--------|--------------|
| **Agent runtime** | Design patterns, tools, overlays, observability | Runtime plugin registry — see [`runtime/docs/`](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/index.md) |
| **Benchmark pipelines** | Custom step types (metrics, plots, reports) | Pipeline `type:` in `experiment.yaml` — see [lab manifests](manifests/pipeline.md) |
| **Evaluation** | Session-level judges and metrics | `mas-library-eval` / MCE integration — see [Tutorial 3](tutorials/03-experiments-and-analysis/README.md) |
| **Deployment** | Controller backends for the lab UI | `mas.lab.controller.plugins` entry-point group |

Use editable installs only when you patch packages in this repository:

```bash
uv pip install -e runtime -e ctl -e library-standard -e library-lab -e library-eval \
  -e lab/components/core -e lab/components/bench -e lab/components/controller -e lab
```

---

## Related

- [User guide](user-guide.md)
- [User configuration](user-config.md)
- [Glossary](glossary.md)

Next: [Labs, libraries, and local plugins](labs-and-libraries.md).
