<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Design pattern contract

`DesignPatternPlugin` is the runtime boundary for a reasoning pattern (ReAct,
Plan-and-Execute, chain-of-thought, single-pass). This page is for contributors
writing a pattern plugin. For enabling a pattern from a manifest, see
[design-patterns.md](../../design-patterns.md).

`DesignPatternContract` is a stable alias of `MealyPlugin`. New plugins subclass
`DesignPatternPlugin`.

```python
from mas.runtime.contracts import DesignPatternPlugin
```

Shipped plugins include `ReactPlugin`, `CotPlugin`, `SinglePassPlugin`, and
Plan-Execute (`library-standard`).

## Kernel API

The kernel invokes design-pattern plugins through:

| Method | Role |
|--------|------|
| `handle_event(q, run, event, config)` | React to ingress (`UserInputReceived`, tool results, ...) |
| `on_user_input` / `on_context_complete` / `on_evaluate` | Phase transitions on `DpState` |
| `evaluate_next` | After engine I/O, schedule next egress (`LLM_CALL`, `TOOL_CALL`, client response) |

Optional `protocol_lines(q)` on a plugin injects phase-specific system
instructions into context.

A design pattern does not replace the Mealy machine. The machine owns runtime
execution states; the pattern owns the reasoning phase inside that lifecycle.

## What the pattern plugin does not own

Model-provider selection, budget policy, session persistence, MAS topology or
delegation routing, and UI behavior belong to the engine, governance, session,
orchestration and facade layers.
