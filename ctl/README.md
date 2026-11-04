<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ctl

> Multi-agent orchestration — compile, compose, chat, validate, run.

---

## Objective

`mas-ctl` manages multi-agent systems described by declarative **MAS manifests**
(`mas.yaml`). It resolves topologies, applies flavours and infra bundles, runs
workflows, and provides the **interactive** CLI (`chat`, `tui`) on top of
`mas-runtime`.

```
mas-runtime  ←  single agent : kernel + contracts + design-pattern plugins
mas-ctl      ←  multi-agent  : compile, compose, chat, run-mas, validate
mas-lab      ←  evaluation   : benchmarks, pipelines, controller UI
```

Benchmarking is **`mas-lab`**, not `mas-ctl`.

---

## Install

```bash
uv sync --all-packages
```

---

## Quickstart

```bash
# Validate / compile manifests
mas-ctl validate agent.yaml
mas-ctl compile agent.yaml -o overlays/tools.yaml

# Interactive single agent
mas-ctl chat agent.yaml -q "Hello"

# Run a MAS
mas-ctl run-mas library-samples/apps/trip-planner/mas.yaml --flavour local

# Inspect workspace / infra
mas-ctl infra list
mas-ctl flavour list
```

---

## Key concepts

### MAS manifest (`mas.yaml`)

Declares agents, workflow, and metadata. See
[docs/manifests/mas.md](../docs/manifests/mas.md).

### Flavour

Selects model endpoint and non-secret configuration (`local`, `mock`, …) from
`library-standard`.

### Scenario

Named overlay for A/B configuration without duplicating manifests.

### Workspace

`$XDG_CONFIG_HOME/mas/config.yaml` and optional project `config.yaml` — see
[docs/user-config.md](../docs/user-config.md) for the full XDG layout.

---

## CLI reference

| Command | What it does | Flags |
|---------|-------------|-------|
| `mas-ctl chat MANIFEST` | Interactive or scripted conversation | [docs/cli/mas-ctl.md](../docs/cli/mas-ctl.md) |
| `mas-ctl tui MANIFEST` | Terminal UI for chat | same · [docs/ctl/tui.md](../docs/ctl/tui.md) |
| `mas-ctl run-mas MANIFEST` | Run a multi-agent workflow | [docs/cli/mas-ctl.md](../docs/cli/mas-ctl.md) |
| `mas-ctl compile …` | Dump resolved Agent/MAS YAML (overlays + defaults) | [docs/cli/compile.md](../docs/cli/compile.md) |
| `mas-ctl compose …` | Compose effective manifests / placement | `mas-ctl compose --help` |
| `mas-ctl plan …` | Dry-run placement plan | `mas-ctl plan --help` |
| `mas-ctl validate PATH …` | Validate agent / MAS / experiment YAML | [docs/cli/mas-ctl.md](../docs/cli/mas-ctl.md) |
| `mas-ctl schemas` | List JSON/YAML schemas | — |
| `mas-ctl flavour list\|show` | List or show flavours | — |
| `mas-ctl infra list\|show` | List or show infra bundles | — |
| `mas-ctl registry …` | Plugin registry introspection | `mas-ctl registry --help` |
| `mas-ctl checkpoint …` | Session checkpoint utilities | `mas-ctl checkpoint --help` |
| `mas-ctl list-bundles` | List library bundles | — |

Overview: [docs/cli/index.md](../docs/cli/index.md).
Workspace YAML: [docs/references/config.yaml.md](../docs/references/config.yaml.md).

---

## Design philosophy

1. **Declarative over imperative** — topology and policies in YAML.
2. **Secrets separated from config** — only `*_env` names in manifests.
3. **Thin CLI** — click handlers call library code in `mas.ctl.*`.
4. **Library-first bench** — `mas-lab` imports ctl session/bootstrap, not subprocesses.

---

## Tutorials

Primary path (MkDocs): **[docs/tutorials/index.md](../docs/tutorials/index.md)**

| # | Tutorial | Topic |
|---|----------|-------|
| 0 | [Environment](../docs/tutorials/00-environment-setup/README.md) | Docker / `uv`, credentials, workspace |
| 1 | [Building an agent](../docs/tutorials/01-building-an-agent/README.md) | `mas-ctl chat`, overlays |
| 2 | [Creating a MAS](../docs/tutorials/02-creating-a-mas/README.md) | `mas.yaml`, `run-mas` |
| 3 | [Experiments](../docs/tutorials/03-experiments-and-analysis/README.md) | `mas-lab benchmark run` |

---

## Examples

| Path | Description |
|------|-------------|
| [library-samples/apps/](../library-samples/apps/) | Trip planner, Q&A, moderator variants |
| [docs/tutorials/](../docs/tutorials/) | Copy-paste tutorial manifests |

Flavours ship in **`library-standard`** and resolve by name:
`mas-ctl run-mas … --flavour local`.

---

## Documentation

| Document | Covers |
|----------|--------|
| [../docs/user-guide.md](../docs/user-guide.md) | Operational guide |
| [../docs/cli/index.md](../docs/cli/index.md) | CLI overview |
| [../docs/cli/mas-ctl.md](../docs/cli/mas-ctl.md) | `mas-ctl` flags (`--trace`, `--events`, …) |
| [../docs/references/config.yaml.md](../docs/references/config.yaml.md) | `config.yaml` fields |
| [../docs/cli/observability.md](../docs/cli/observability.md) | `events.jsonl` |
| [../docs/manifests/](../docs/manifests/README.md) | YAML manifests |
| [../docs/libraries.md](../docs/libraries.md) | Package matrix |

---

## Related packages

| Package | Role |
|---------|------|
| [`mas-runtime`](../runtime/) | Kernel, contracts, design patterns |
| [`mas-lab`](../lab/) | Benchmarks, pipelines, UI |
| [`library-standard`](../library-standard/) | Flavours, overlays, infra |
