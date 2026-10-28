<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Documentation map (contributors)

**This file is not published on GitHub Pages** (`exclude_docs` in `mkdocs.yml`).

## One source tree

All user documentation lives under **`docs/`**. The published site is
`mkdocs build` of this folder, driven by [`mkdocs.yml`](../mkdocs.yml)
at the repository root. Intended public URL:
[ioc-project.github.io/mas-lab](https://ioc-project.github.io/mas-lab/).

| Where | What |
| ------- | ------ |
| [docs/index.md](index.md) | Site home — MAS-Lab in the Internet of Cognition |
| [docs/what-is/index.md](what-is/index.md) | Toolkit overview (former home page) |
| [docs/getting-started.md](getting-started.md) | Install and first run (from the README) |
| [mkdocs.yml](../mkdocs.yml) `nav:` | Left sidebar and top tabs |
| Theme | IoC chrome (`docs/stylesheets/ioc.css`) + MAS-Lab tab bar (`ioc-layout.css`) |
| Header GitHub facts | Repo name, `VERSION`, stars, and forks — baked by [`scripts/mkdocs_github_stats.py`](../scripts/mkdocs_github_stats.py) at `mkdocs build`. Pages rebuilds nightly (`.github/workflows/pages.yml`). |

Local preview: `task docs-serve` → <http://127.0.0.1:8000>

When you add or rename a doc page, update **`mkdocs.yml` nav** and cross-links
so every page under `docs/` that should be public is reachable from a tab.

Left panel rule: **one section level, then pages**. Do not nest a section
inside a section. Section labels are yellow kickers; page titles are the
items. The tab name is not repeated in the drawer.

User guide holds a **small set of topics**. Flag tables, schemas, and other
exhaustive material live under **References** and are linked from the topic
pages. Protocol adapters (OpenAI, MCP, A2A, Agent Skills, OpenTelemetry) live
under **Open standards**. `navigation.path` renders ancestor breadcrumbs above the H1.

---

## Site navigation (mirrors `mkdocs.yml`)

### Home

- [MAS-Lab](index.md) — IoC-BHAG landing; no left sidebar

### Getting started

- [What is MAS-Lab](what-is/index.md) — tab landing
- Setup → [Install](getting-started.md), [Docker](getting-started/docker.md), [From source](getting-started/developer.md)
- Concepts → [Architecture](architecture.md), [Repo structure](what-is/repo.md), [The problem](overview.md), [Experiments](what-is/experiments.md), [Packages](what-is/packages.md), [Surfaces](what-is/surfaces.md)

### User guide

- [Overview](user-guide.md)
- Guides → [Writing manifests](manifests/writing-manifests.md), [Labs vs libraries](labs-and-libraries.md), [User configuration](user-config.md)
- [Command line](cli/index.md) — topic index; flags are under References
- Interfaces → [Web UI](ui/index.md), [Live dashboard](ui/live.md), [TUI](ctl/tui.md)
- [Glossary](glossary.md)

### Discover

- [Discover](discover/index.md) — tab index
- [IoC integrations](discover/integrations.md)
- Tutorials → [index](tutorials/index.md), 0–6 and 8–14
- Labs → [Overview](discover/labs.md), [paper labs](discover/labs.md#paper-labs)

### Open standards

- [Overview](standards/index.md)
- [OpenAI](standards/openai.md), [MCP](standards/mcp.md), [A2A](standards/a2a.md), [Agent Skills](standards/agent-skills.md), [OpenTelemetry](standards/opentelemetry.md)
- Internals (linked, not a kicker): [A2A internals](a2a/developer.md)

### References

- [References index](references/index.md)
- CLI → [mas-runtime](cli/mas-runtime.md), [mas-ctl](cli/mas-ctl.md), [mas-lab](cli/mas-lab.md), [compile](cli/compile.md), [overrides](cli/overrides.md), [observability](cli/observability.md), [controller API](cli/serve.md), [live-reference](ui/live-reference.md)
- Design notes → HITL, flavour boundary, manifest libraries, compaction
- Specifications → `manifests/*.md`, [config.yaml](references/config.yaml.md), [schemas](references/schemas.md)
- Runtime → [context management](references/context-management.md), [event model](references/event-model.md), [communication headers](references/comm-headers.md), [communication stack](references/comm-stack.md), [manifests/runtime.md](manifests/runtime.md), [runtime docs](references/runtime.md)
- Lab → experiment/dataset/pipeline manifests, [lab bench](references/lab.md), [library discovery](library-discovery.md)
- Packages → [package map](libraries.md), [packages](packages-reference.md), [plugins](plugins-reference.md)

### Paper

- [Paper labs & reproducibility](paper/index.md) — no left sidebar

### Blog

- [All posts](blog/index.md) · categories **Updates** / **Releases**

---

## Outside `docs/` (linked, not on Pages)

| Path | Audience |
| ------ | ---------- |
| [`docker/README.md`](https://github.com/ioc-project/mas-lab/blob/main/docker/README.md) | Docker compose, mounts, env |
| [`ui/README.md`](https://github.com/ioc-project/mas-lab/blob/main/ui/README.md) | UI package development (Yarn, Vite) |
| [`runtime/docs/`](https://github.com/ioc-project/mas-lab/tree/main/runtime/docs) | Runtime contributor docs |
| Internal extensions (evolution, KG, rust) | Not published in OSS — separate internal repository |

---

## Contributing

[CONTRIBUTING.md](https://github.com/ioc-project/mas-lab/blob/main/CONTRIBUTING.md) at the repository root.
