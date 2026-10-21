<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Production execution path

The kernel envelope path is the only supported way to run agents in production. This page is for developers who embed or extend the kernel.

```python
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine

instance = RuntimeInstance.from_parts(engine=SimulatedEngine())
trace = instance.run_user_text("Hello")
```

The path is:

```
RuntimeInstance → KernelDriver → RuntimeKernel (envelope contracts)
```

## Components

| Layer | Module | Role |
|-------|--------|------|
| Embed API | `mas.runtime.driver.instance.RuntimeInstance` | Session surface: `run_user_text`, pause/resume/abort, snapshots |
| Driver loop | `mas.runtime.driver.driver.KernelDriver` | Closes the Mealy loop: feeds Σ_in, auto-dispatches Σ_out to engine, HITL, and context |
| Kernel | `mas.runtime.kernel.orchestrator.RuntimeKernel` | Pure transition function: ingress symbol → egress symbol list |
| Envelope | `mas.runtime.kernel.envelope` | Governance, observability, and contract execution on every crossing |

## Data flow

1. **Ingress** — `UserInputReceived`, `EngineIoReturn`, `HitlResolve`, or lifecycle symbols enter via `RuntimeInstance.feed()` / `KernelDriver.feed()`.
2. **Kernel transition** — `RuntimeKernel.transition()` updates `QProduct` state and emits egress symbols (`InvokeEngineIo`, `EmitHitlRequest`, `RequestCtxAssembly`, etc.).
3. **Driver dispatch** — `KernelDriver` executes side effects (LLM/tool engine, HITL responder, context assembly) and feeds results back as ingress.
4. **Envelope** — Each engine crossing runs through `EnvelopeContext` and `run_contract_execute_obs` / `run_ingress_validate_envelope` before commit.

## Building a configured runtime

For configured agents, use `RuntimeBuilder` (`mas.runtime.factory.builder`) to wire manifest, engine, and HITL adapters into a `RuntimeInstance`.

## What is not the production path

- **`HookRegistry` / `BaseAgent` / `skeleton/`** — removed; do not reintroduce a parallel hook-plane.
- **`topology/`** — removed; workflow routing belongs in design-pattern plugins and manifest delegation config.
- **gRPC transport** — no production gRPC transport is shipped; the `grpc` extra only installs the client libraries.

## Related docs

- [Mealy envelope](mealy-envelope.md)
- [Automaton product model](automaton-product-model.md)
- [Design patterns](design-patterns.md)
