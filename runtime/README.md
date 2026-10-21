<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-runtime

> Contract-driven Mealy kernel for LLM agents.

---

## Objective

`mas-runtime` is the **core runtime library** of MAS Lab. It provides contract
interfaces (tool, context, memory, control, recorder, …), a v2 **kernel** with
seven-symbol egress/ingress envelopes, design-pattern plugins (ReAct, Plan-Execute,
…), and governance chokepoints — all **framework-agnostic**.

The runtime instruments agents without replacing your orchestration framework.
Governance and observability run at kernel chokepoints so policies apply
consistently across LangChain, LangGraph, AutoGen, and custom loops.

---

## Stack position

```text
mas-runtime  ←  embeddable Mealy kernel (library only — no CLI)
mas-ctl      ←  control plane: compose, chat, run-mas, validate (instantiates runtime)
mas-lab      ←  evaluation: benchmarks, pipelines, controller UI
```

**All CLIs:** `mas-ctl` (agents) and `mas-lab` (benchmarks). Use `mas-ctl chat` for interactive and headless scripted runs (`-q`).

---

## Setup

### Install (from source)

```bash
uv sync --package mas-runtime   # kernel library only
uv sync --package mas-ctl       # required for chat / compose CLIs
uv sync --all-packages          # full monorepo
```

### Credentials

Secrets are never embedded in manifests. Set environment variables or use a
`.env` file (see [docs/user-config.md](../docs/user-config.md)):

```bash
export OPENAI_API_KEY=sk-...
```

### Infrastructure and flavours

Manifests reference **infra bundles** and **flavours** from `library-standard`
(`local`, `mock`, `local-benchmark`, …). Only secret *names* appear in YAML
(`api_key_env: OPENAI_API_KEY`).

See [docs/plugin-flavours.md](docs/plugin-flavours.md).

### Plugin aliases

`mas-library-standard` ships the canonical built-in plugin implementations and the runtime alias defaults.
The runtime registry maps human-friendly names and aliases such as `react` or `planner` to canonical URNs
such as `mas.dp.react`.

Runtime alias defaults live in [src/mas/runtime/aliases.yaml](src/mas/runtime/aliases.yaml).
The schema is [src/mas/runtime/aliases.schema.yaml](src/mas/runtime/aliases.schema.yaml), and
`mas.runtime.registry.aliases.validate_alias_manifest()` validates both the package manifest and
`config.yaml` alias overrides.

See [docs/plugin-aliases.md](docs/plugin-aliases.md) for discovery order, alias resolution, and configuration examples.

---

## Quickstart

**Recommended (interactive):**

```bash
mas-ctl validate agent.yaml
mas-ctl chat agent.yaml -q "What is the capital of France?"
```

**Headless / Docker / CI:**

```bash
mas-ctl chat agent.yaml -q "What is the capital of France?"
```

**Library API:**

```python
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.ctl.session.controller import SessionController, run_session_loop
from mas.ctl.ui.stdout import StdoutConversationDisplay

opts = InstantiationOptions.from_manifest_path("agent.yaml")
instance, _ = instantiate_runtime(opts)
controller = SessionController(instance=instance, display=StdoutConversationDisplay())
run_session_loop(controller, scripted=["Hello"])
```

---

## Core concepts

| Layer | What it does |
|-------|----------------|
| **Kernel** | `RuntimeKernel` + `KernelDriver` — schedules egress, runs envelopes |
| **Envelope** | Authorize → execute → validate on every `LLM_CALL` / `TOOL_CALL` |
| **Design patterns** | `ReactPlugin`, `PlanExecutePlugin`, … via `evaluate_next` / `handle_event` |
| **Contracts** | Stable plugin boundaries — see [contracts-reference.md](docs/contracts-reference.md) |
| **Governance** | Policy at egress chokepoints — see [governance-policy-engine.md](docs/governance-policy-engine.md) |

Status checklist: [docs/mealy-envelope.md](docs/mealy-envelope.md).

---

## Tutorials

**Primary learning path (MkDocs):** [docs/tutorials/index.md](../docs/tutorials/index.md)
at the repository root — Tutorials 0–3 cover environment, agent, MAS, and experiments.

**Runtime deep-dives** (contributor-oriented, in this tree):

| Topic | Document |
| --- | --- |
| Design patterns | [design-patterns.md](docs/design-patterns.md) |
| Plugin flavours | [plugin-flavours.md](docs/plugin-flavours.md) |
| Plugin authoring | [plugin-and-tool-authoring.md](docs/plugin-and-tool-authoring.md) |
| Mealy product model | [automaton-product-model.md](docs/automaton-product-model.md) |

---

## Examples

| Path | Description |
|------|-------------|
| [library-samples/apps/](../library-samples/apps/) | Trip planner, Q&A, and other sample apps |
| [docs/tutorials/01-building-an-agent/](../docs/tutorials/01-building-an-agent/) | Step-by-step agent tutorial (MkDocs) |

---

## Documentation

### Users

| Document | Answers |
|----------|---------|
| [docs/user-guide.md](docs/user-guide.md) | Install, run, integrate |
| [docs/contracts-reference.md](docs/contracts-reference.md) | Which contract to implement |
| [docs/plugin-flavours.md](docs/plugin-flavours.md) | Dev vs prod configuration |
| [docs/design-patterns.md](docs/design-patterns.md) | CoT / ReAct / Plan-Execute |
| [docs/context-segmentation.md](docs/context-segmentation.md) | Context assembly and provenance |
| [docs/semantic-protocols.md](docs/semantic-protocols.md) | Governance scopes and URNs |
| [docs/naming-standards.md](docs/naming-standards.md) | Naming conventions |

### Contributors

| Document | Covers |
|----------|--------|
| [docs/index.md](docs/index.md) | Documentation hub |
| [docs/production-path.md](docs/production-path.md) | Kernel execution path |
| [docs/mealy-envelope.md](docs/mealy-envelope.md) | Envelope implementation status |
| [docs/automaton-product-model.md](docs/automaton-product-model.md) | Formal product model (design + map) |
| [docs/developer-guide.md](docs/developer-guide.md) | Plugin and test workflow |
| [docs/trajectory-schema.md](docs/trajectory-schema.md) | Event / trace schema |

---

## Testing

```bash
uv run pytest runtime/tests/ -q
uv run pytest runtime/tests/ --cov=mas.runtime --cov-report=term-missing
```

---

## Related packages

| Package | Role |
|---------|------|
| [`mas-ctl`](../ctl/) | Compose, chat, run-mas, validate |
| [`mas-lab`](../lab/) | Benchmarks, pipelines, UI |
| [`library-standard`](../library-standard/) | Flavours, overlays, infra bundles |
