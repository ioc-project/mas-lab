<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# sample_governance — GovernancePlugin + IngressGovernancePlugin

Use the alias `sample_governance` in manifests (resolves via plugin registry).

```yaml
governance:
  - sample_governance:
      hitl_on_tool: true          # HITL before tool runs (egress)
      hitl_on_tool_result: true   # HITL before result enters working memory (ingress)
      hitl_mode: interactive      # auto-approve | auto-deny for CI
```

| Attribute | Meaning |
|-----------|---------|
| `hitl_on_tool` | Pause for operator before `TOOL_CALL` executes |
| `hitl_on_tool_result` | Pause before tool output is committed to working memory and shown to the model (user-facing: "context") |
| `hitl_mode` | Who answers HITL prompts (`interactive` = TTY operator) |

Optional kernel profile fields (`gov_policy_profile`, `gov_trigger_destructive`) apply only when **not** using explicit `hitl_on_tool` — prefer the boolean flags above for tutorials.

On ingress SKIP, optional **steering text** replaces the tool result body before it lands in working memory.
HITL `BLOCK` refuses the operation and ends the turn with that denial as the user-visible answer. Optional steering is appended to the canned denial. The model does not get another call to substitute a result. `SKIP` still continues the model turn.
