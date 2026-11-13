<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# gov_no_undeclared_peer — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `gov_no_undeclared_peer@v1` |
| **Alias** | `gov_no_undeclared_peer`, `no_undeclared_peer` |
| **Kind** | governance |
| **URN** | `mas.gov.no_undeclared_peer` |
| **Implementation** | `NoUndeclaredPeerPlugin` in `mas.library.standard.plugins.governance.no_undeclared_peer` |
| **Manifest keys** | `spec.governance` |
| **Control** | `pkg://mas.library.standard/controls/with-hardened.yaml` |

Blocks a leftover `delegate_to_*` `TOOL_CALL` whose dest is not this agent's
`delegates_to`. Send itself does not refuse. Native `events.jsonl` records
`kind: governance_decision` with `decision: BLOCK` and
`policy_name: gov_no_undeclared_peer`.

## Why

`delegates_to` is the leftover tool list the model sees. Without this plugin
a `TOOL_CALL` for another dest still runs. `with-hardened` names this plugin
so that dest lock is governance, the same place undeclared tool names are
refused.

A linear, parallel, or single pattern still sends: those calls are leftover
`delegate_to_*` names from the same list.

## Manifest

```yaml
governance:
  - gov_no_undeclared_peer
```

Or apply the standard Control:

```bash
mas-ctl chat agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml \
  --control pkg://mas.library.standard/controls/observability-native.yaml
```

| Attribute | Meaning |
|-----------|---------|
| (none) | No config. Allowed dests are this agent's `delegates_to`. An empty list BLOCKs leftover send. Other tool names PASS. |

Overlay index: [../../overlays/README.md](../../overlays/README.md).
User reference: [governance.md](../../../../../../../docs/manifests/governance.md).
