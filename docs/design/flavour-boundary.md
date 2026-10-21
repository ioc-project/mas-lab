<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->

# Flavour / infra / overlay / execution boundary

Status: implemented. This note records why the boundary sits where it does. For the supported fields, use the [Flavour manifest reference](../manifests/flavour.md).

## The problem

Four concerns look alike in code, because each ends up patching an in-memory spec dict. They are different:

- A **flavour** is a deployment preset. It sets protocol, observability and control plugin selection, and tool exposure.
- **Infra** holds endpoints, API-key variable names, the tool name-to-implementation mapping, and model wire names.
- An **overlay** is a small YAML file merged on top of a manifest. The **agent spec** holds LLM inference parameters, tools, skills, and memory.
- **Execution** holds the engine queue, LLM cache policy, streaming, and parallel tool calls.

Cache is the awkward case. It is shaped like infrastructure: it is an `InfraMiddleware` in the engine's infra pipeline (`runtime/src/mas/runtime/engine/infra_pipeline.py`). But whether it is on is a per-run choice, not a fixed property of the environment.

Example: one run records with the cache on, another replays. Both use the same flavour and infra. Only the execution settings differ.

## Decision

1. **Execution is not a flavour concern.** Queue depth, LLM disk-cache policy, streaming, and parallel tool dispatch live in `kind: RuntimeEngine` (`infra/v1`). The runtime resolves it from workspace `runtime_refs` or `--runtime-ref`. Agent, MAS, and Overlay manifests may not carry these settings. Offline LLM turns use the `llm_cache` middleware recorded against a live provider, not a flavour field.
2. **Endpoints are infra.** The `infra.schema.yaml` kinds (`InfraBundle`, `LLMProxy`, `ToolProvider`, `ToolRegistry`, ...) own endpoints, API-key variable names, wire names, and tool mapping. A flavour may not carry `infra_refs` or `infra_ref`.
3. **A flavour carries posture only.** `FlavourSeparationValidator` (`ctl/src/mas/ctl/validate/separation.py`) rejects `spec.llm`, `spec.skills`, `spec.prefer_local`, `spec.infra_refs`, and `spec.infra_ref`. Inference parameters and skills are Agent concerns. `prefer_local` duplicated workspace infra tuning and was removed.
4. **Overlay is the one patch mechanism.** Flavours and agents use the same JSON merge-patch semantics. `merge_overlay` (`ctl/src/mas/ctl/overlay/merge.py`) dispatches on the overlay's `spec.target.kind`:
    - `Agent` to `merge_agent_overlay`.
    - `MAS` to `merge_mas_overlay`.
    - `Flavour` to `merge_flavour_overlay`.
    - `Infra` to a plain merge-patch.

    `merge_flavour_overlay` is deliberately narrow. It rejects any patch key that is not a property of the Flavour schema. An overlay therefore cannot reintroduce model or skill state through a flavour.
5. **`observability` and `control` use one shape everywhere.** Each is a closed plugin list, such as `[native]` or `[native, {otel: {output_path: traces/otel}}]`. See `fragments/observability-binding.schema.yaml` and `fragments/control-binding.schema.yaml`. An overlay can choose which known plugins run and set their few sanctioned options. It cannot invent a plugin id or pass arbitrary plugin configuration. The `--events*` CLI flags apply as a Flavour overlay patch on `observability` (`ctl/src/mas/ctl/cli/obs_flags.py`).

## Consequences

Each concern has one owner and one mechanism.

| Concern | Owner | Mechanism |
|---|---|---|
| Endpoints, API keys, model wire names, tool name-to-implementation mapping | **Infra** (`infra/v1`: `InfraBundle`, `LLMProxy`, `ToolProvider`, ...) | `--infra-ref` or workspace `infra_refs` only. Never Agent, MAS, or overlay YAML. |
| Protocol, observability and control plugin *selection* | **Flavour** | `--flavour NAME`, optionally patched by an overlay with `target.kind: Flavour`. |
| LLM inference parameters, tools, skills, memory | **Agent spec / overlay** | `merge_agent_overlay`, CLI overlay builders. |
| Engine queue, LLM disk-cache policy, streaming, parallel tool calls | **RuntimeEngine** (`infra/v1`) | Workspace `runtime_refs`, `--runtime-ref`. |
| Trace replay and batch concurrency | **Experiment** `design`, `schedule`, `bench_emulation` (mas-lab only) | [experiment.md](../manifests/experiment.md#design-vs-schedule) |

## See also

- [Flavour manifest](../manifests/flavour.md)
- [Overlay manifest](../manifests/overlay.md)
- [Infra manifest](../manifests/infra.md)
- [LLM cache reference](../references/llm-cache.md)
