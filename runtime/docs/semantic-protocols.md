<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Semantic protocols

URNs, governance scopes, and stable identifiers across manifests, plugins, and
traces.

---

## Plugin and contract URNs

Plugins register with stable URNs:

```text
mas.dp.react
mas.cm.stack
mas.gov.no_undeclared_tool
```

Manifests reference plugins by **alias** (`react`, `stack`) or full URN.
The registry (`mas.runtime.registry`) resolves aliases to implementation
classes (see [plugin-aliases.md](plugin-aliases.md)). Prefer URNs in published overlays for reproducibility.
`mas-ctl registry list` prints the registered URNs.

---

## Governance scopes

Semantic governance attaches **scopes** to decisions recorded in traces:

| Scope | Applies to |
|-------|------------|
| `egress` | Outbound LLM, tool, memory, transport |
| `ingress` | Inbound model/tool results |
| `delegation` | Agent-to-agent messages |
| `lifecycle` | Pause, abort, checkpoint |

Each `governance.decision` event in `events.jsonl` includes `hook`,
`checkpoint` (`before` / `after`), `decision` (`ALLOW`, `BLOCK`, `HITL`, …),
and `policy_name`.

HITL scopes use `HitlQuestionType` — user resolution flows through
`mas-ctl chat` / TUI.

---

## Manifest identifiers

| Field | Convention |
|-------|------------|
| `metadata.name` | Lowercase kebab (`trip-planner`) |
| `spec.agency.agents[].id` | Stable agent id in a MAS manifest |
| Tool names | Snake or kebab matching `ToolContract.get_name()` |
| Overlay names | Overlay file name without extension |

See [naming-standards.md](naming-standards.md) for span names and event kinds.

---

## Trace correlation

- **correlation_id** — ties one egress envelope (authorize → execute → validate).
- **user_turn_id** — user message boundary (`t1`, `t2`, …).
- **trace_id** — session-level root for OpenTelemetry export.

Downstream bench steps group metrics by these ids. Do not reuse correlation ids
across parallel tool calls in the same turn.

---

## Related docs

- [governance-policy-engine.md](governance-policy-engine.md)
- [dev/contracts/governance.md](dev/contracts/governance.md)
- [trajectory-schema.md](trajectory-schema.md)
