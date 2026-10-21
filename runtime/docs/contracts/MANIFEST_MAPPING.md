<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Manifest mapping

How YAML manifest fields bind to runtime contracts and kernel modules.

---

## Agent manifest (`kind: Agent`)

| Manifest path | Binds to |
|---------------|----------|
| `spec.models[]` | Model slots + `LLMProvider` routing |
| `spec.tools[]` | `ToolContract` plugins |
| `spec.design_pattern` | DP plugin id (`react`, `plan_execute`, …) |
| `spec.context` | Context budget, facets |
| `spec.governance[]`, `spec.observability[]`, `spec.control` | Schema-declared governance, observability, and control bindings |

Resolution: `mas.ctl.runtime_cli.load_merged_agent_manifest` →
`instantiate_runtime`.

---

## MAS manifest (`kind: MAS`)

| Manifest path | Binds to |
|---------------|----------|
| `spec.agency.agents[]` | Agent manifest references or inline Agent documents |
| `spec.workflow.entry`, `spec.workflow.nodes[]` | Entry point, participants, delegation targets |

Resolution: `mas.ctl.compose` → `run-mas`.

---

## Experiment manifest (`experiment.yaml`)

| Field | Binds to |
|-------|----------|
| `dataset` | Bench dataset loader |
| `scenarios` | Overlay matrix (one scenario = one set of overlays) |
| `run` | `n_runs`, per-run artifacts and hooks |
| `execution` | Batch concurrency, timeouts, and ordering |
| Level `pre` / `post` steps | `mas.lab.benchmark.pipeline` step types |

Entry: `mas-lab benchmark run`.

---

## Flavour and infra

| Artifact | Role |
|----------|------|
| `flavour/*.yaml` | Protocol, observability, control, and tool deployment posture |
| `infra/*.yaml` | Provider endpoints, tool servers, middleware, and data connections |
| `config.yaml` | Workspace defaults: infra refs, default model, aliases |

Schema: [docs/schemas/config.schema.yaml](../../../docs/schemas/config.schema.yaml).

---

## Related

- [DESIGN_SPACE.md](DESIGN_SPACE.md)
- [../../../docs/manifests/README.md](../../../docs/manifests/README.md)
