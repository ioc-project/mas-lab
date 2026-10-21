<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Contract taxonomy

A contract is a Python protocol for one runtime boundary. This page is for contributors who must choose
the right contract for a new runtime feature. It classifies contracts into three families and gives a selection guide.

```python
from mas.runtime.contracts import ToolContract, MemoryContract, RecorderContract
```

## The three families

`mas.runtime.contracts.base` distinguishes three top-level families:

| Family | Meaning | Typical question | Examples |
| --- | --- | --- | --- |
| Capability | What the runtime can access or expose | What operation is available? | `ToolContract`, `MemoryContract`, `RecorderContract` |
| Orchestration | How control flows across agents | How are agents invoked and stitched together? | `DelegationContract` |
| Governance | What is allowed | Is this operation permitted right now? | `BudgetTracker`, governance plugins |

The split keeps capabilities reusable, orchestration focused on topology, and governance cross-cutting.

## Chokepoints and contracts

A chokepoint is a runtime execution site. A contract is a developer-facing protocol.
Several contracts can share one chokepoint, so the runtime has more contracts than chokepoints:

- The tool chokepoint can host `ToolContract` and human-in-the-loop (HITL) plugins.
- The model chokepoint can host the LLM provider and `RecorderContract`.
- The state chokepoint can host `MemoryContract` and session state.

See [mealy-hooks-and-closure.md](mealy-hooks-and-closure.md) for the closure model.

## Contract, plugin, implementation type

| Term | Meaning | Example |
| --- | --- | --- |
| Contract | Abstract runtime boundary | `ToolContract` |
| Plugin | Concrete component implementing a contract | tool-server-backed tool provider |
| Implementation type | Data structure or helper used by a plugin | `SessionState`, `BudgetTracker` |

## Selection guide

| If the feature is about... | Prefer... | Avoid... |
| --- | --- | --- |
| Accessing a resource or service | A capability contract | Encoding policy into the resource contract |
| Denying or constraining access | A governance contract | Embedding denials inside every tool or provider |
| Running agents in a different topology | An orchestration contract | Coupling loop behavior to a capability plugin |
| Prompt contribution | `ContextContract` | Ad hoc mutation of `messages[]` in many plugins |
| Conversation history trimming | `ContextManagerContract` | Mutating session history from many plugins |
| Durable run checkpoints | Session snapshot APIs | Flattening run state into conversation history |
| Cross-agent shared state | Coordination / blackboard plugins | Abusing `MemoryContract` for coordination locks |

## Boundaries that are easy to confuse

| Boundary | Stores | Scope |
| --- | --- | --- |
| Session | Durable conversation history and metadata | Contact or channel session |
| Run snapshot | In-flight run state and checkpoint data | Single run |
| `MemoryContract` | Semantic, episodic or procedural memory | Agent memory layer |
| Shared context plugins | Multi-agent coordination state | Group or MAS-level coordination |

`ContextContract` contributes typed context parts to prompt assembly: it says what is present.
`ContextManagerContract` trims or compresses past turns before the next LLM call: it says how much history survives.
Its result is still a protocol `messages[]` (roles and tool ids), not a token string. See
[ContextManagerContract](state-and-context.md#contextmanagercontract) for the pairing rules plugins must keep.

## Hook-driven execution

The registry calls many contracts through hooks instead of direct imports:

```text
Runtime event
  -> registry executes pre_* hooks
  -> capability contract method runs
  -> registry executes post_* hooks
```

`ToolContract.call_tool()` is surrounded by `pre_tool_call` and `post_tool_call`.
LLM completions are surrounded by `pre_llm_call` and `post_llm_call`.
