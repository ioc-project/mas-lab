<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Contract developer reference

A contract is a Python protocol that defines one stable runtime boundary. A plugin implements one or more contracts.
This section is for contributors who add plugins, runtime surfaces or MAS-facing facades.
Start with [taxonomy.md](taxonomy.md) to choose the right boundary, then use the inventory below.

```python
from mas.runtime.contracts import ToolContract, MemoryContract, RecorderContract
```

## Shipped contracts

Importable from `mas.runtime.contracts` unless a module is given:

| Contract ID | Class | Category | Purpose | Reference |
| --- | --- | --- | --- | --- |
| `tool` | `ToolContract` | Capability | Tool listing and invocation | [model-and-tools.md](model-and-tools.md#toolcontract) |
| `memory` | `MemoryContract` | Capability | Memory read and write | [model-and-tools.md](model-and-tools.md#memorycontract) |
| `recorder` | `RecorderContract` | Capability | Structured event recording | [execution-control-and-observability.md](execution-control-and-observability.md#recordercontract) |
| `context` | `ContextContract` | Capability | Typed prompt context contribution | [state-and-context.md](state-and-context.md#contextcontract) |
| `context_manager` | `ContextManagerContract` | Supporting | Conversation-history trimming | [state-and-context.md](state-and-context.md#contextmanagercontract) |
| `dp` | `DesignPatternPlugin` | Capability | Design-pattern scheduler | [design-patterns.md](design-patterns.md) |
| `engine` | `EngineContract` | Capability | LLM, tool, and memory I/O | [model-and-tools.md](model-and-tools.md) |
| `ctx_assembler` | `CtxAssembler` | Capability | Context assembly | [state-and-context.md](state-and-context.md) |
| `observability` | `ObservabilitySink` | Capability | Trace and event emission | [execution-control-and-observability.md](execution-control-and-observability.md) |
| `cm_factory` | `CMFactory` | Capability | Context-manager factory | [state-and-context.md](state-and-context.md) |
| `control` | `ControlContract` (`mas.runtime.boundary.control.contract`) | Capability | Pause, resume, steer | [execution-control-and-observability.md](execution-control-and-observability.md#controlcontract) |
| `delegation` | `DelegationContract` (`mas.runtime.boundary.delegation.protocol`) | Capability | Agent-as-tool delegation | [messaging-and-orchestration.md](messaging-and-orchestration.md#delegationcontract) |
| `budget` | `BudgetTracker` (`mas.runtime.boundary.gov.budget`) | Governance | Token and call ceilings | [governance.md](governance.md) |

## Recommended reading order

[taxonomy.md](taxonomy.md), [model-and-tools.md](model-and-tools.md), [state-and-context.md](state-and-context.md),
[messaging-and-orchestration.md](messaging-and-orchestration.md),
[execution-control-and-observability.md](execution-control-and-observability.md),
[governance.md](governance.md), [design-patterns.md](design-patterns.md),
[mealy-hooks-and-closure.md](mealy-hooks-and-closure.md).

Call paths for tools, models, orchestration and delegation are in [contracts-reference.md](../../contracts-reference.md#call-paths).
