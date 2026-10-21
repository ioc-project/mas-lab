<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Contracts

A *contract* is a fixed interface between the kernel and the code it calls. The *kernel* is the core of `mas-runtime`. Read this page to learn which boundaries exist before you write a plugin. Other terms are in the [glossary](../glossary.md).

## The problem

The kernel must call tools, models, memory, and governance without knowing their implementations. A contract names what the kernel may call. A *plugin* implements one or more contracts. You swap a plugin without touching the kernel.

Example: a manifest picks a design pattern by name. The kernel calls it through the `DesignPatternPlugin` contract.

```yaml
spec:
  design_pattern: react
```

The machine-readable registry is [`contracts-registry.yaml`](../schemas/contracts-registry.yaml). It lists ingress and egress events and the Python modules for each registered contract.

---

## Implemented in this release (OSS)

These boundaries are wired in the kernel. Tutorials and paper labs exercise them.

| Contract / plugin | Role |
| --- | --- |
| `ToolContract` | Tool listing and invocation. See the [reference](tool-contract.md). |
| `MemoryContract` | Memory read and write at the engine boundary. |
| `ContextContract` / `ContextManagerContract` | Prompt and conversation context. |
| `DesignPatternPlugin` | ReAct, chain-of-thought, and single-pass lifecycles. |
| `EngineContract` | LLM, tool, and memory I/O. |
| `LLMProvider` | Chat-completion wire protocol (OpenAI-compatible, routed like tools). Static knobs: [`spec.models[]`](../manifests/agent.md) sampling, `reasoning`, `extra`, and the [catalog](../manifests/llm-model-catalog.md). Dynamic kwargs: `stream`, `stream_options`, `tool_choice`, `extra_body`, `extra_headers`, `extra_query`. |
| `CtxAssembler` | Context and prompt assembly. |
| `ObservabilitySink` / `EventEmitter` | Trace and JSONL event emission. |
| `BudgetTracker` + budget overlays | Stateful token and cost governance (`lifecycle-control.lab`). |

### Manifest-selected Protocol categories

Library packages declare `PluginRegistry` categories. A manifest selects a plugin by name. Current categories include `tool_provider`, `governance`, `hitl_contract`, `user_io_contract`, `skill_impl`, and the design and context categories.

- `hitl_contract` implements `HITLContract.request_approval(...)`. HITL means human in the loop.
- `user_io_contract` implements `UserIOContract.send_progress_update(...)`.
- Both default to `registry` in `mas-library-standard`.
- A caller-supplied `InstantiationOptions.hitl_contract` or `.user_io_contract` instance wins over the manifest. This keeps interactive CLI adapters working.

Governance ingress plugins register under the `governance` category. Select them in `spec.governance[].ingress_plugins` with `type` (or `name`) and optional `chain: stop|continue`. They receive the ingress intent snapshot. They cannot access mutable kernel state.

### `engine_tool_provider` and `EngineToolContext`

Orchestration-level tools run another agent instead of calling a function. They register under `engine_tool_provider`. `mas-library-standard` ships two variants: `delegation` (existing peers) and `spawn_subagent` (new children from pre-authored templates).

A plugin in this category never receives the materialized run or the comm bus. It receives an `EngineToolContext`. The context exposes three operations and three read-only properties:

| Member | Purpose |
| --- | --- |
| `run_turn(agent_id, task, ...)` | Run one turn on an agent of this run. |
| `spawn_instance(manifest, *, template_id, manifest_dir=None)` | Materialize a child and return its minted id. |
| `teardown_instance(agent_id)` | Release the child, its working memory, and its ledger depth. |
| `depth` | Current spawn depth for the session. |
| `session_id` / `parent_agent_id` | Identity of the calling scope. |

The budget check lives inside `spawn_instance`, not in its callers. A third-party plugin therefore meets the same `SpawnLedger` ceilings as a shipped one. At a ceiling, the facade raises `EngineToolBudgetExceeded`. It does not return a value a plugin could ignore.

A plugin that holds this object cannot enumerate other agents, read another agent's working memory, or send arbitrary bus messages. The facade does not expose them.

Manifest-facing behaviour is unchanged. `spec.behavior.allow_subagent_spawning` and `spec.behavior.delegation_style` still decide whether the tool surface exists. This category changes only how the runtime finds and builds the implementing class.

---

## Taxonomy

The runtime groups boundaries into three families.

| Family | Meaning | Examples |
| --- | --- | --- |
| **Capability** | What the runtime can access or expose. | `ToolContract`, `MemoryContract`, `RecorderContract` |
| **Orchestration** | How control flows across agents | `DelegationContract` |
| **Governance** | What is permitted | `BudgetTracker`, governance plugins |

For method signatures and implementation detail, see the [developer contract reference](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/index.md) in the repository. You need a clone to read it.

---

## Contract groups (developer reference)

| Group | Topics |
| --- | --- |
| Models, tools, memory | Tool and model access, prompt assembly, memory backends |
| Context | Prompt context contribution and history trimming |
| Messaging & orchestration | Delegation |
| Control & observability | Execution control, recorder / trace emission |
| Governance | Budget, policy engine, HITL |
| Design patterns | ReAct, chain-of-thought, single-pass lifecycles |

Deep dives on GitHub: [model & tools](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/model-and-tools.md)
·
[state & context](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/state-and-context.md)
·
[messaging & orchestration](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/messaging-and-orchestration.md)
·
[execution control](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/execution-control-and-observability.md)
·
[governance](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/governance.md)
·
[design patterns](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/dev/contracts/design-patterns.md)

---

## Governance (OSS)

*Governance* is the policy layer that decides whether a call may proceed. User reference: [governance.md](../manifests/governance.md) (chain, plugins). Retries, circuit breaker, `error_policy`, defaults, and logging: [reliability.md](reliability.md).

Stateful governance plugins enforce policy across turns. The paper **lifecycle-control** lab stacks budget caps, guardrails, and HITL through overlays, for example `budget-cap` on `budget_threshold`.

Runtime implementation: `BudgetTracker` in `runtime/src/mas/runtime/boundary/gov/budget.py`.

Egress and ingress decisions on every tool, LLM, and memory call pass through two layers, evaluated in order:

1. **Declarative policies.** `GovernancePolicyEngine` (`runtime/src/mas/runtime/boundary/gov/policy_engine.py`) matches YAML-authored `PolicyDefinition`s against the call. A policy has a trigger condition and an `action`: `hitl`, `block`, `terminate`, `log`, `modify`, `skip`, `retry`, or `blacklist`. The engine reads `spec.governance` in the agent or overlay manifest. A matched policy's own `params.message` (or `params.reason`) becomes the reported reason. Otherwise the engine builds a reason from the policy name and action.
2. **Parametric profiles.** `GovPolicyProfile` (egress) and `GovIngressProfile` (ingress) apply when no declarative policy matches. They are fixed postures such as `block-destructive`, `hitl-destructive`, and `retry-on-error`. Implementation: `resolve_egress_governance`, `egress_governance_outcome`, and `ingress_governance_outcome` in `runtime/src/mas/runtime/boundary/gov/policy.py`.

Every decision is emitted as a `governance_decision` observability event with `hook`, `checkpoint`, `decision`, `reason`, and `policy_name`. The Governance lane and per-call badges of the multilevel trajectory plot render these events. See [pipeline-processors.md, Multilevel trajectory plot](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/pipeline-processors.md#multilevel-trajectory-plot).

---

## Plugin registry (v2)

The registry maps contract IDs to Python modules and, where applicable, to Mealy and TLA machines. A Mealy machine is a state machine whose output depends on its state and input. The registered contracts in this release:

| ID | Role |
| --- | --- |
| `DesignPatternPlugin` | Reasoning pattern scheduler (ReAct, CoT, single-pass) |
| `EngineContract` | LLM, tool, and memory I/O |
| `CtxAssembler` | Prompt / context assembly |
| `HitlResponder` | Human-in-the-loop approval |
| `ObservabilitySink` | Trace and event emission |
| `EnvelopeProduct` | Contract-call envelope (governance + observability wrap) |
| `EventEmitter` | JSONL and stderr event sinks (`mas-library-standard`) |
| `ObservabilityExportPlugin` | Manifest-declared export plugins (`native` in `mas-library-standard`, `otel` in `mas-library-telemetry`) |
| `LLMProvider` | Chat-completion wire protocol (`OpenAILLMProvider`, `CacheLLMProvider`) |
| `EventTransform` | Boundary to native or OTel transforms |

See [`contracts-registry.yaml`](../schemas/contracts-registry.yaml) for aliases, ingress and egress events, and bundled implementations.

---

## Typical call paths

In the shipped kernel, budget checks run as governance (`BudgetTracker`). Every call goes through the envelope (`mas.runtime.kernel.envelope`): governance authorize, `EngineContract` I/O, then observability.

### Tool execution

```text
Runtime or planner
  -> ToolContract.list_tools()     # required: name, description, parameters
  -> BudgetTracker.allow_tool()
  -> ToolContract.call_tool(name, arguments)   # required two-arg invocation
  -> RecorderContract.emit()
```

Optional advertise keys and `call_tool` kwargs are documented in
[tool-contract.md](tool-contract.md). Everyday calls use the required two-argument form.

### Model execution

```text
Runtime
  -> BudgetTracker.allow_llm()
  -> EngineContract / LLMProvider
  -> RecorderContract.emit()
```

### Delegation

```text
Runtime
  -> DelegationContract.list_tools() and collect_context()
  -> DelegationContract.delegate()
```

---

## See also

- [Runtime manifest fields](../manifests/runtime.md): for YAML authors.
- [Schema index](schemas.md): validation schemas.
- [ToolContract](tool-contract.md): `call_tool(name, arguments)`.
- [ToolServerRegistry](tool-server-registry.md): infra connection policy.
- [Thin waist](thin-waist.md): how names resolve to plugins.
- [Mealy machines guide](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/mealy-machines-guide.md)
- [Glossary](../glossary.md): vocabulary.
- [ADR 0002](adr-0002-observability-event-model.md): why the `ObservabilitySink` / `EventEmitter` boundary records an announced stream of events instead of exposing a query interface.
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
