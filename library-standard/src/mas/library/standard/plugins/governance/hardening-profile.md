<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# gov_hardening_profile — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `gov_hardening_profile@v1` |
| **Alias** | `gov_hardening_profile`, `hardening_profile` |
| **Kind** | governance |
| **URN** | `mas.gov.hardening_profile` |
| **Implementation** | `HardeningProfilePlugin` in `mas.library.standard.plugins.governance.hardening_profile` |
| **Manifest keys** | `spec.governance` (a Control) |

The run-time half of the [hardening profile](../../../../../../../docs/references/hardening.md).
`mas-ctl` judges a session before it starts; this plugin judges what only exists while it runs.

- At the first egress of the session it records the hardening report as one `LOG` decision
  (`governance_decision` in `events.jsonl`): which checks passed, failed, were waived or are
  checked per call.
- Under `profile: production` it BLOCKs a `TOOL_CALL` whose arguments carry an `http(s)` or `ws(s)`
  url to a host that is not on `allowed_hosts`. Without `allowed_hosts` it judges no host.

Declare it in a Control; it has no effect when it is not declared.

```yaml
apiVersion: control/v1
kind: Control
metadata: {name: production-egress}
spec:
  governance:
    - gov_hardening_profile:
        profile: production
        allowed_hosts: [api.example.com, "*.internal.example.com"]
        waive: []          # [{check: egress-host-allowlist, reason: "..."}] records instead of blocking
```

| Attribute | Meaning |
|-----------|---------|
| `profile` | `production` (default once declared) blocks. `default` only records. |
| `allowed_hosts` | Host names or `*.suffix`, compared on the whole host. A bare `*`, a port, a path or an empty list is refused at start. |
| `waive` | `[{check: egress-host-allowlist, reason}]`. The call is allowed and the reason is recorded. |
| `report` | Set by `mas-ctl` from the session-start report. Do not write it. |

The walk over the arguments is bounded (256 strings, depth 8), so a hostile argument tree cannot stall the gate.
