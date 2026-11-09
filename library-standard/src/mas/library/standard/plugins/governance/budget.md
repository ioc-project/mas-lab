<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# budget — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `budget@v1` |
| **Alias** | `budget` |
| **Kind** | governance |
| **URN** | `mas.gov.budget` |
| **Implementation** | `BudgetPlugin` in `mas.library.standard.plugins.governance.budget` |
| **Manifest keys** | Control `spec.governance` |

Counts the LLM calls and tool calls a run issues and TERMINATEs the run once a
ceiling is reached. A field left unset has no limit.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_llm_calls` | integer ≥ 0 | unset | Maximum LLM calls in a run. |
| `max_tool_calls` | integer ≥ 0 | unset | Maximum tool calls in a run. |

```yaml
apiVersion: control/v1
kind: Control
metadata: {name: capped}
spec:
  governance:
    - budget: {max_llm_calls: 50, max_tool_calls: 100}
```

It replaces the deprecated Agent `spec.budget`. It passes every other operation, so
it chains with other governance plugins; order on a transition is `priority`.
