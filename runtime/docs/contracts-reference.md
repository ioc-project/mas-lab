<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Contracts reference

A contract is a Python protocol that defines one stable runtime boundary, such as tools, memory or recording.
This page is for plugin authors who need to know which contracts exist and which one to implement.
For method signatures and examples, see the [contract developer reference](dev/contracts/index.md).

A plugin implements one or more contracts. The runtime calls contracts through direct method calls,
hook dispatch or registry-driven composition.

## Families

| Family | Meaning | Examples |
| --- | --- | --- |
| Capability | What the runtime can access or expose | `ToolContract`, `MemoryContract`, `RecorderContract` |
| Orchestration | How control flows across agents | `DelegationContract` |
| Governance | What is permitted | `BudgetTracker`, governance plugins |

The importable list is in [dev/contracts/index.md](dev/contracts/index.md#shipped-contracts);
the rationale is in [dev/contracts/taxonomy.md](dev/contracts/taxonomy.md).

`ContextManagerContract` is documented with state and context because it operates on conversation history.
It is a supporting interface, not a top-level family. Its payload rules (protocol versus tokens, tool-call pairing)
are in [state-and-context.md](dev/contracts/state-and-context.md#contextmanagercontract).

## Call paths

Tool execution:

```text
Runtime or planner
  -> ToolContract.list_tools()
  -> BudgetTracker.allow_tool()
  -> ToolContract.call_tool()
  -> RecorderContract.emit()
```

Model execution:

```text
Runtime
  -> BudgetTracker.allow_llm()
  -> EngineContract / LLMProvider
  -> RecorderContract.emit()
```

Delegation:

```text
Runtime
  -> DelegationContract.list_tools() and collect_context()
  -> DelegationContract.delegate()
```

## Where to read next

| If you want to... | Start here |
| --- | --- |
| Understand the family split | [taxonomy.md](dev/contracts/taxonomy.md) |
| Implement a tool or model provider | [model-and-tools.md](dev/contracts/model-and-tools.md) |
| Work on sessions, memory boundaries or prompt assembly | [state-and-context.md](dev/contracts/state-and-context.md) |
| Work on delegation or MAS topology | [messaging-and-orchestration.md](dev/contracts/messaging-and-orchestration.md) |
| Work on control or recording | [execution-control-and-observability.md](dev/contracts/execution-control-and-observability.md) |
| Work on policy enforcement | [governance.md](dev/contracts/governance.md) |
| Work on reasoning patterns | [design-patterns.md](dev/contracts/design-patterns.md) |
