<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Design space (10 dimensions)

Orthogonal dimensions of the agent automaton. Each dimension maps to manifest
fields and/or kernel summands.

| # | Dimension | Manifest / config | Runtime realization |
|---|-----------|-------------------|---------------------|
| 1 | **Model access** | `spec.models`, infra | `EngineContract`, LLM egress |
| 2 | **Tools** | `spec.tools`, tool plugins | `ToolContract`, TOOL_CALL envelope |
| 3 | **Memory** | `spec.memory` | `MemoryContract`, memory egress |
| 4 | **Context** | `spec.context`, CM plugins | `ContextContract`, assembly |
| 5 | **Design pattern** | `spec.design_pattern` | DP plugin scheduler |
| 6 | **Governance** | `spec.governance` | Egress chokepoint + policy engine |
| 7 | **Observability** | `spec.observability`, events flags | Obs envelope, `events.jsonl` |
| 8 | **Session** | session / checkpoint options | Turn log, checkpoints |
| 9 | **Control** | `spec.control`, HITL, pause, steer | `ControlContract`, ctl UI |
| 10 | **Topology** | MAS `workflow` (multi-agent) | Delegation, transport |

Dimensions compose by **parallel product** (⊗) where independent, **variant
choice** (⊔) for DP/CM selection, and **envelope composition** (∘) per egress.

---

## Shipped subset

The production kernel instantiates a **fixed triple** envelope composer
(`M_obs ⊗ M_gov ⊗ M_capability`) plus the DP scheduler and context assembly.
Full ⊗ⁿ families for governance and observability are design targets — see
[automaton-product-model.md](../automaton-product-model.md) §16.

---

## Related

- [MANIFEST_MAPPING.md](MANIFEST_MAPPING.md)
- [../dev/contracts/taxonomy.md](../dev/contracts/taxonomy.md)
