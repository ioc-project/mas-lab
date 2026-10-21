<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Mealy, hooks and contract closure

This page explains how contracts, hooks, chokepoints and the Mealy machine fit together, and defines closure.
It is for contributors deciding whether a change needs a new contract or a new hook.
The short answer: five chokepoint families do not limit the runtime to five contracts.

- A chokepoint is a runtime execution site that every critical operation must pass through.
- A contract is a typed protocol exposed to developers and implemented by plugins or facades.

```text
Mealy machine
  -> reaches a runtime chokepoint
  -> runtime dispatches hooks around that chokepoint
  -> one or more contracts participate at that chokepoint
```

Several contracts share a chokepoint family:

- Tool: `ToolContract`, `BudgetTracker`, HITL plugins.
- Model: the engine (`EngineContract`), budget governance, `RecorderContract`, cache plugins.
- State: `MemoryContract` and kernel session state.

## The five chokepoint families

| Chokepoint family | Runtime meaning | Base operation |
| --- | --- | --- |
| Execution | Entering or leaving one runtime step or run | execution lifecycle |
| Model | Calling an LLM or model provider | `llm_call()` |
| Tool | Executing an external tool or action | `tool_call()` |
| Communication | Sending work or messages across agent boundaries | `send_message()` |
| State | Reading or mutating durable or shared state | `memory_read()`, `memory_write()` and higher-level state services |

In the Python code, scheduled egress operations run through the kernel envelope (`mas.runtime.kernel.envelope`)
and engine IO (`mas.runtime.engine`).

## Roles

The Mealy machine owns execution lifecycle and state transitions, not every plugin API. It decides when the
runtime is in an execution, reasoning, acting, waiting, interrupted or terminated state.

| Concern | Role |
| --- | --- |
| Mealy machine | Execution-state evolution |
| Chokepoints | Mandatory runtime crossings |
| Hooks | Interception and observability around those crossings |
| Contracts | Developer-facing typed interfaces |

Developers program against contracts. The runtime routes contracts through a fixed set of chokepoints.
The Mealy machine and hooks stay internal, which keeps the boundaries portable to a multi-process deployment.

## Closure

The target is perfect closure:

1. Every critical side effect passes through a canonical chokepoint.
2. Every chokepoint dispatches the corresponding hooks.
3. Every plugin interacts with the runtime through contracts or runtime facades, not concrete backends.
4. Bypassing a chokepoint is an architectural violation.

The current architecture is closure-oriented, not perfectly closed.

Already in place:

- Scheduled egress and ingress crossings run through the envelope in
  [kernel/envelope.py](../../../src/mas/runtime/kernel/envelope.py).
- State transitions are a single pure function in
  [kernel/orchestrator.py](../../../src/mas/runtime/kernel/orchestrator.py) (`RuntimeKernel.transition`).
- Governance decisions are made at the egress and ingress gates under
  [boundary/gov/](../../../src/mas/runtime/boundary/gov/).
- The design-pattern contract that schedules operations is in
  [machines/design_pattern/protocol.py](../../../src/mas/runtime/machines/design_pattern/protocol.py).

Not yet closed in Python:

- A plugin can still import and call a lower-level component directly.
- Not all stateful operations go through one normalized state facade.
- Several domain-specific hooks exist in parallel with the chokepoint model.

## Why memory is not a tool call

Memory differs from a tool: it is part of the agent's internal or semi-internal state, it needs provenance and
versioning, it benefits from interpretability and replay, and it changes what the agent later knows rather than
performing an external action. `MemoryContract` therefore stays distinct from `ToolContract`.

## Rules for new contracts and hooks

Add a contract only if all of these hold:

1. It models a stable developer-facing protocol.
2. It participates at a canonical chokepoint, or justifies a new one.
3. It has at least two plausible implementations or backends.
4. A plugin, profile or facade above an existing contract cannot express it cleanly.

Add a hook only if at least one of these holds:

1. A canonical chokepoint lacks interception.
2. The existing hook family cannot express a required policy or observability guarantee.
3. The hook corresponds to a state transition the Mealy machine must expose.

Otherwise reuse an existing chokepoint hook. The target is a small fixed set of chokepoints, a bounded set of
typed contracts that plug into them, and tests that treat bypass paths as architectural failures. Existing extra
hooks stay until justified; the runtime converges toward a small canonical set.
