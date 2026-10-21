<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Library Samples

Reusable sample artefacts (apps, datasets, tools, skills, overlays, infra manifests) for tutorials and labs. Use it when you need a ready-made MAS or manifest to run or reference; it holds no runnable experiments.

YAML samples live here, grouped by artefact type. Experiments belong only under `labs/*.lab/`: a lab is the experiment pack, this tree is a library. When to create each: [docs/labs-and-libraries.md](../docs/labs-and-libraries.md).

Quick start, from the repository root:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-samples/overlays/hitl-on-tool.yaml
```

## What belongs here

| Kind | Examples |
|------|----------|
| **apps** | `apps/trip-planner/` (real use-case MAS: agents, local tools/skills), `apps/qa-mas/` (small question-answering MAS) |
| **datasets** | `datasets/trip-planner/benchmark.yaml` — complete pack for the app, tutorials, and CI |
| **tools** | `tools/calc.py`, `tools/*.tool.yaml` (trip-planner domain tools: see [tools/README.md](tools/README.md)) |
| **skills** | `skills/answer-formatting/` (shared); app-specific skills live under the app tree |
| **overlays** | `overlays/cot-moderator.yaml`, `overlays/hitl-on-tool.yaml`, … |
| **infra** | `infra/tool-providers.yaml`, `infra/llm-cache-{write,replay}.yaml`, MCP / A2A deployment manifests |
| **pipelines**, **experiments** | `pipelines/pipeline-test.yaml`, `experiments/moderator-design-patterns.yaml` (small reference files, not paper experiments) |
| **sample-workspace** | `sample-workspace/config.yaml` — OSS dev/CI workspace default |

Generic pipelines, built-in steps, and shared artefacts belong in **`library-standard`** (or future lab-standard libraries), not here.

## What does *not* belong here

- **`experiment.yaml`** — only in `labs/<name>.lab/`
- Lab-specific pipeline definitions — colocate with the lab (`labs/.../pipeline-figure.yaml`, inline in experiment)
- One-off benchmark output paths or canvas exports
- Small plugin/feature examples — those sit in
  `library-standard/examples/<category>/` (not under `apps/`)

Labs **compose** library artefacts by registered id — not path traversal. In an
`experiment.yaml`:

```yaml
experiment:
  application:
    app: trip-planner          # id from this library's library.yaml `apps:`
    configs_dir: ./overlays
  dataset:
    name: trip-planner-benchmark
    locator: samples           # mas.runtime.manifest_libraries scheme
    # limit: 5                 # optional: first N items (smoke / CI)
```

Install `mas-library-samples` (or `uv pip install -e library-samples`) so the
`samples` locator resolves via its entry point.

Labs may also declare `libraries: [samples]` under `lab:` in `lab-config.yaml` to
inject the library root at run time (see `labs/design-space.lab/*/lab-config.yaml`).

## Referencing from tutorials

From the repository root, pass an overlay by path:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-samples/overlays/hitl-on-tool.yaml
```

Tool refs inside manifests use the library scheme instead of a path, for example
`ref: samples:tools/calc.tool.yaml` (see
[Tutorial 14](../docs/tutorials/14-governance-hitl/README.md)). Copy an overlay
into a tutorial's `overlays/` folder for a self-contained bundle.
