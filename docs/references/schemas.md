<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Schema index

This page lists the YAML and JSON schemas that `mas-ctl validate`, `mas-lab check`, and the benchmark UI use. A *schema* states which keys a manifest may hold and what type each has. The files live under [`docs/schemas/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas/).

Example: check an agent manifest against its schema.

```bash
mas-ctl validate agent.yaml
```

Human-readable field docs are in [Specifications](index.md#specifications).

## Hand-written vs generated

Every file under `docs/schemas/` is **hand-written**. It is the single source of truth. Nothing in that folder is generated. These artifacts derive from it:

| Generated file | Generator | Inputs |
| --- | --- | --- |
| `runtime/src/mas/runtime/spec/schema_bindings_generated.py` (binding key sets) | `python scripts/gen_schema_artifacts.py` (`--check` fails when stale) | `runtime/fragments/*-binding`, `context-manager-*-params`, `infra-*-params` |
| `runtime/src/mas/runtime/spec/schema_defaults_generated.py` (default constants) | same | `execution-binding`, `context-manager-*-params`, `agent.schema.yaml` |
| `ui/src/types/agent-types.ts`, `ui/src/types/mas-types.ts` | `yarn generate:types` in `ui/` | Resolved `agent` / `mas` schemas from `mas-lab serve` `GET /api/schemas/{id}?resolved=1` |

Two hand-written runtime package schemas live outside this folder: `runtime/src/mas/runtime/defaults.schema.yaml` (built-in `defaults.yaml`) and `runtime/src/mas/runtime/aliases.schema.yaml` (plugin alias table).

## Overlay and CLI merge metadata

`x-merge` annotations define collection identity and merge behaviour. They apply to both YAML overlays and CLI `--override` patches.

- CLI overrides become canonical `kind: Overlay` documents.
- The runtime validates them against the target schema.
- They apply last.
- Root-qualified paths identify the document, for example `agent:spec.tools`, `mas:spec.agency.agents[id=qa].spec.memory`, or `experiment:experiment.run.n_runs`.

The canonical overlay schema is [`runtime/overlay.schema.yaml`](../schemas/runtime/overlay.schema.yaml). Its target-specific patch fragments are [`overlay-agent-patch.schema.yaml`](../schemas/runtime/fragments/overlay-agent-patch.schema.yaml),
[`overlay-mas-patch.schema.yaml`](../schemas/runtime/fragments/overlay-mas-patch.schema.yaml),
[`overlay-infra-patch.schema.yaml`](../schemas/runtime/fragments/overlay-infra-patch.schema.yaml),
and [`overlay-flavour-patch.schema.yaml`](../schemas/runtime/fragments/overlay-flavour-patch.schema.yaml).
The [Overlay manifest reference](../manifests/overlay.md) defines `target.kind`,
`target.name`, `spec.patch`, and grouped `spec.overrides` together.

---

## Workspace and project

| Schema | Purpose |
| --- | --- |
| [`config.schema.yaml`](../schemas/config.schema.yaml) | Root `config.yaml`. See the [field reference](config.yaml.md) (`mas_ctl`, `mas_lab`, infra refs, `manifest_libraries:`) |
| [`library.schema.yaml`](../schemas/library.schema.yaml) | `kind: Library` (`library.yaml`). See [labs-and-libraries.md](../labs-and-libraries.md) |
| [`deployment.schema.yaml`](../schemas/deployment.schema.yaml) | Deployment / placement descriptors |
| [`runtime-profile.schema.yaml`](../schemas/runtime-profile.schema.yaml) | Runtime profile flags (governance, observability) |
| [`placement-plan.schema.yaml`](../schemas/placement-plan.schema.yaml) | Component placement plan |
| [`effective-bind.schema.yaml`](../schemas/effective-bind.schema.yaml) | Resolved binding after overlay merge |
| [`memory-seed.schema.yaml`](../schemas/memory-seed.schema.yaml) | Dataset memory seed files |
| [`checkpoint.schema.yaml`](../schemas/checkpoint.schema.yaml) | Execution checkpoint payloads |

Checkpoint payload version 1 contains the kernel snapshot. Version 2 also contains session lineage, working memory, and a hash-verified embedded agent manifest. See [Session checkpoints](../manifests/checkpoint.md).

---

## Runtime manifests

Top-level kinds (see [manifest reference](../manifests/README.md)):

| Schema | `kind` / role |
| --- | --- |
| [`runtime/agent.schema.yaml`](../schemas/runtime/agent.schema.yaml) | `Agent` |
| [`runtime/llm-model-catalog.schema.yaml`](../schemas/runtime/llm-model-catalog.schema.yaml) | Curated per-model context and settings. See [llm-model-catalog.md](../manifests/llm-model-catalog.md) |
| [`runtime/mas.schema.yaml`](../schemas/runtime/mas.schema.yaml) | `MAS` |
| [`runtime/workflow.schema.yaml`](../schemas/runtime/workflow.schema.yaml) | `Workflow` |
| [`runtime/overlay.schema.yaml`](../schemas/runtime/overlay.schema.yaml) | `Overlay` |
| [`runtime/flavour.schema.yaml`](../schemas/runtime/flavour.schema.yaml) | `Flavour` |
| [`runtime/infra.schema.yaml`](../schemas/runtime/infra.schema.yaml) | Infra bundle, middleware, ToolServerRegistry. See [infra.md](../manifests/infra.md) and the [reference](tool-server-registry.md). |
| [`runtime/tool.schema.yaml`](../schemas/runtime/tool.schema.yaml) | Tool definition. See [tool.md](../manifests/tool.md) |
| [`runtime/fragments/infra-tool-server.schema.yaml`](../schemas/runtime/fragments/infra-tool-server.schema.yaml) | Infra `tool_servers[]` item. See [tool-server-registry.md](tool-server-registry.md) |
| [`runtime/tool_bundle.schema.yaml`](../schemas/runtime/tool_bundle.schema.yaml) | Bundled tools |
| [`runtime/prompt_bundle.schema.yaml`](../schemas/runtime/prompt_bundle.schema.yaml) | Prompt bundles |

Shared fragments live under [`runtime/fragments/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas/runtime/fragments/). They cover bindings, workflow nodes and edges, policy rules, and design-pattern config.

---

## Lab and benchmarks

| Schema | Purpose |
| --- | --- |
| [`lab/experiment.schema.yaml`](../schemas/lab/experiment.schema.yaml) | `experiment.yaml` |
| [`lab/dataset.schema.yaml`](../schemas/lab/dataset.schema.yaml) | Dataset manifest |
| [`lab/pipeline.schema.yaml`](../schemas/lab/pipeline.schema.yaml) | Pipeline step definitions |
| [`lab/lab.schema.yaml`](../schemas/lab/lab.schema.yaml) | Lab folder metadata |
| [`lab/lab-config.schema.yaml`](../schemas/lab/lab-config.schema.yaml) | Lab config, including `lab.libraries` |
| [`lab/run-input.schema.yaml`](../schemas/lab/run-input.schema.yaml) | Per-run inputs |
| [`lab/pipeline-manifest.schema.json`](../schemas/lab/pipeline-manifest.schema.json) | Serialized pipeline manifest |
| [`lab/artefacts/run_info.schema.json`](../schemas/lab/artefacts/run_info.schema.json) | Run metadata artefact |
| [`lab/artefacts/metrics.schema.json`](../schemas/lab/artefacts/metrics.schema.json) | Metrics artefact |

Shared lab fragments live under [`lab/fragments/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas/lab/fragments/): `memory-seed-entry`, `message` (conversation message), `ref` (file ref), and `tool-fixture-mapping`.

---

## Contracts, components, and evaluation

| Schema | Purpose |
| --- | --- |
| [`contracts-registry.yaml`](../schemas/contracts-registry.yaml) | Plugin contract registry. See [Contracts](contracts.md). |
| [`component-registry.yaml`](../schemas/component-registry.yaml) | Embedder-chosen component ids (runtimes, placement backends, framework adapters); manifest plugins resolve through `PluginRegistry` instead. See [Thin waist](thin-waist.md). |
| [`evaluation-envelope.schema.yaml`](../schemas/evaluation-envelope.schema.yaml) | Evaluation result envelope |

---

## Examples

Sample overlays and bindings under [`schemas/examples/`](https://github.com/outshift-open/mas-lab/tree/main/docs/schemas/examples/):

- [`overlays/live-llm.yaml`](../schemas/examples/overlays/live-llm.yaml)
- [`overlays/llm-reasoning.yaml`](../schemas/examples/overlays/llm-reasoning.yaml)
- [`overlays/llm-sampling.yaml`](../schemas/examples/overlays/llm-sampling.yaml)
- [`overlays/observability-native.yaml`](../schemas/examples/overlays/observability-native.yaml)
- [`infra/mcp-localhost.yaml`](../schemas/examples/infra/mcp-localhost.yaml)
- [`library-samples/infra/mcp-localhost-deploy.yaml`](https://github.com/outshift-open/mas-lab/blob/main/library-samples/infra/mcp-localhost-deploy.yaml): MCP server deployment posture
- [`tools/annotated.tool.yaml`](../schemas/examples/tools/annotated.tool.yaml)
- [`checkpoint-axis.yaml`](../schemas/examples/checkpoint-axis.yaml)
- [`checkpoint-v2.json`](../schemas/examples/checkpoint-v2.json)
- [`subagent-agent.yaml`](../schemas/examples/subagent-agent.yaml) ·
  [`subagent-reviewer.yaml`](../schemas/examples/subagent-reviewer.yaml)
- [`datasets/mmlu-pro.yaml`](../schemas/examples/datasets/mmlu-pro.yaml) ·
  [`datasets/mmlu-pro-jsonl.yaml`](../schemas/examples/datasets/mmlu-pro-jsonl.yaml)
  (with `mmlu-pro-slice.jsonl`)
- [`experiments/design-schedule.yaml`](../schemas/examples/experiments/design-schedule.yaml)

The deployment, effective-bind, placement, memory-seed, and runtime-profile schemas also have `*.example.yaml` siblings, including `deployment.local-inproc.example.yaml`.

## See also

- [Manifest overview](../manifests/README.md)
- [Contracts](contracts.md)
- [config.yaml reference](config.yaml.md)
- [Tutorial 1: building an agent](../tutorials/01-building-an-agent/README.md)
