<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# gov_no_undeclared_tool — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `gov_no_undeclared_tool@v1` |
| **Alias** | `gov_no_undeclared_tool`, `no_undeclared_tool` |
| **Kind** | governance |
| **URN** | `mas.gov.no_undeclared_tool` |
| **Implementation** | `NoUndeclaredToolPlugin` in `mas.library.standard.plugins.governance.no_undeclared_tool` |
| **Manifest keys** | `spec.governance` |
| **Control** | `pkg://mas.library.standard/controls/with-hardened.yaml` |
| **Example** | [examples/governance/undeclared-tool/](../../../../../../examples/governance/undeclared-tool/) (Agent; not a sample app) |

Blocks a `TOOL_CALL` whose name was not in the OpenAI `tools` array of the
LLM call that produced it — even if `spec.tools` lists that name. The kernel
feeds the policy reason back as a tool observation so the same agent can
pick a listed name. Native `events.jsonl` records
`kind: governance_decision` with `decision: BLOCK` and
`policy_name: gov_no_undeclared_tool`.

## Why

A skill or prompt can mention a tool the **MAS** owns while this **agent**
was not given that function on this call. Models will still emit the name.
Without this plugin the call either crashes the turn or executes a tool the
model was never offered.

## Example (feature scenario)

[examples/governance/undeclared-tool/](../../../../../../examples/governance/undeclared-tool/)
is a runnable Agent, not a sample app: listed tools omit `get_deployments`,
the skill names it. `llm_call.json` is the recorded chat-completions
payload for the same mismatch. Index: [examples/](../../../../../../examples/README.md).

```bash
mas-ctl validate library-standard/examples/governance/undeclared-tool/agent.yaml
mas-ctl chat library-standard/examples/governance/undeclared-tool/agent.yaml \
  -q "Investigate the latency spike for payment-service."
```

The plugin BLOCKs an undeclared name with:

```
Tool 'get_deployments' was not in the tools list offered to you.
Do not call it. Available tools: get_metrics, get_logs, get_service_health.
```

## Manifest

```yaml
governance:
  - gov_no_undeclared_tool
```

Stack in `spec.governance` as an iptables-style *chain* (observability is a
*sequence* — every plugin always runs). ALLOW passes to the next plugin;
BLOCK exits the chain and returns that error. A plugin that does not apply
MUST pass. HITL is a hold: the chain keeps walking so a later BLOCK still
fires:

```yaml
governance:
  - sample_governance:
      hitl_on_tool: true
  - gov_no_undeclared_tool
```

Or apply the standard overlay (appends; does not replace existing plugins):

```bash
mas-ctl chat agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml \
  --control pkg://mas.library.standard/controls/observability-native.yaml
```

The example agent already lists `gov_no_undeclared_tool` on its chain and
`native` observability, so `mas-ctl chat agent.yaml` is enough.

| Attribute | Meaning |
|-----------|---------|
| (none) | No config. Allowed names are the last LLM `tools` list, else `spec.tools`. `None` (not recorded) fail-opens. An empty offer `()` BLOCKs every tool name. |

Overlay index: [../../overlays/README.md](../../overlays/README.md).
User reference: [governance.md](../../../../../../../docs/manifests/governance.md).
