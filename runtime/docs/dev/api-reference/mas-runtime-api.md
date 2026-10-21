<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-runtime API Reference

Library API for the Mealy kernel, contracts and embeddable runtime instances. This page is for developers who embed the kernel in tests, benchmarks or custom hosts; to run agents, use `mas-ctl chat` or `mas-ctl run-mas`.

## Overview

| Layer | Package | Primary types |
| --- | --- | --- |
| Kernel | `mas.runtime` | `RuntimeKernel`, `IngressSymbol`, `EgressSymbol`, `StepResult` |
| Embed | `mas.runtime.factory.builder` | `RuntimeBuilder` → `RuntimeInstance` |
| Session (recommended) | `mas.ctl` | `instantiate_runtime`, `SessionController` |

---

## Kernel (`mas.runtime`)

The public package exports kernel types only:

```python
from mas.runtime import (
    RuntimeKernel,
    StepResult,
    IngressSymbol,
    EgressSymbol,
    QProduct,
    LifecycleState,
    DpState,
)
```

`RuntimeKernel` orchestrates one agent step: ingress symbols → envelope → egress symbols.
See [mealy-envelope.md](../../mealy-envelope.md) and [automaton-product-model.md](../../automaton-product-model.md).

---

## RuntimeBuilder (embed)

`RuntimeBuilder` constructs a `RuntimeInstance` from `KernelConfig` and optional adapters
(engine, HITL responder, context assembler):

```python
from mas.runtime.factory.builder import RuntimeBuilder
from mas.runtime.kernel.config import KernelConfig

instance = RuntimeBuilder(
    config=KernelConfig(),
    enable_governance=True,
    enable_observability=True,
).build()

trace = instance.driver.run_turn("Hello")
print(trace.final_text)
```

`RuntimeInstance` exposes `driver` (`run_turn`, `reset_session`) and the underlying kernel product.

---

## Session bootstrap (`mas.ctl`)

Ctl owns manifest validation, memory seeds, checkpoints, and observability wiring:

```python
from pathlib import Path

from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.ctl.session.controller import ConversationConfig, SessionController, run_session_loop
from mas.ctl.ui.stdout import StdoutConversationDisplay

options = InstantiationOptions(
    agent_manifest={...},  # or load via manifest_dir + path
    manifest_dir=Path("path/to/manifests"),
    validate_manifests=True,
)
instance, checkpoint_store = instantiate_runtime(options)

controller = SessionController(
    instance=instance,
    display=StdoutConversationDisplay(),
    checkpoint_store=checkpoint_store,
    config=ConversationConfig(),
)
result = controller.run_turn("What is the capital of France?")
print(result.text)
run_session_loop(controller, interactive=False, scripted=[])
```

### `InstantiationOptions`

| Field | Purpose |
| --- | --- |
| `pattern_plugin_id` | Design-pattern plugin (default from agent manifest) |
| `memory_seed_path` | Optional `memory_seed` YAML |
| `checkpoint_dir` | Json checkpoint store directory |
| `agent_manifest` | Parsed agent dict |
| `manifest_dir` | Base path for refs and skills |
| `resolved_infra` | Composed infra from `mas-ctl compose` |
| `workspace` | `WorkspaceConfig` from `config.yaml` |
| `enable_observability` / `enable_governance` / `enable_coordination` | Kernel feature flags |

---

## SessionController

Shared control plane for CLI, curses TUI, and REST adapters:

```python
controller.run_turn(user_text)       # → TurnResult(trace, responses, awaiting_hitl)
controller.reset_session()           # clear working memory
run_session_loop(controller, interactive=True)  # interactive REPL when wired to a display
```

`TurnResult.text` joins client-facing response content from egress symbols.

---

## Contracts and plugins

Implement contracts under `mas.runtime.contracts.*` and register via `@plugin` or the plugin registry.
Authoring guide: [plugin-and-tool-authoring.md](../../plugin-and-tool-authoring.md).

| Contract family | Examples |
| --- | --- |
| Design pattern | `DesignPatternContract` — ReAct, CoT, plan-execute |
| Context manager | `ContextManagerContract` — stack, sliding window, summarizing |
| Tool | `ToolContract` — `pre_tool_call`, `post_tool_call` |
| Governance | Envelope chokepoints (σ₂, σ₆) — see [production-path.md](../../production-path.md) |
| Observability | Span emission on all envelope symbols |

---

## Manifests and overlays

Manifest loading and overlay merge live in `mas.ctl` (`validate_file`, `merge_overlay`).
Runtime receives composed snapshots only; ctl applies external state.

```python
from mas.ctl.overlay import merge_overlay, normalize_overlay

overlay = normalize_overlay({
    "apiVersion": "mas/v1",
    "kind": "Overlay",
    "spec": {"patch": {"llm": {"temperature": 0.2}}},
})
merged = merge_overlay(base_agent, overlay)
```

Overlays must use canonical `mas/v1` `Overlay` shape with `spec.patch` (no shorthand).

---

## What is not in this package

| Removed / never shipped in v2 OSS | Use instead |
| --- | --- |
| `AgentBuilder` / `AgentRuntime` | `RuntimeBuilder` + `SessionController` |
| Hook-plane dispatch | Mealy envelope + kernel chokepoints |
| Direct `runtime.py` orchestration loop | `RuntimeKernel` + `RuntimeInstance.driver` |

---

## See also

- [mas-ctl CLI](../cli/cli-reference.md)
- [contracts-reference.md](../../contracts-reference.md)
- [Tutorial 01 — Building an agent](../../../../docs/tutorials/01-building-an-agent/README.md)
