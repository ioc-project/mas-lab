<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# environment — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `environment@v1` |
| **Name** | `environment` |
| **Kind** | governance |
| **URN** | `mas.gov.environment` |
| **Implementation** | `EnvironmentPlugin` in `mas.library.standard.plugins.governance.environment` |
| **Manifest keys** | A lab-generated Control names it; you do not add it by hand |

`environment@v1` is an observer. It always ALLOWs. At each governance
transition it asks the run's environment whether a trigger matches, and
waits while that trigger's world actions run (revoke a credential, advance
the clock, sleep). With no environment bound, or with no kernel-facing
triggers, it does not subscribe: the driver treats an empty filter list
as "everything", so the plugin returns a filter that matches nothing.

It never writes kernel state. World actions that change the session go
through the control API, not through this plugin's decision.

Put deciding plugins (budget, guardrails) *before* `environment@v1` in a
Control so a BLOCK is decided before the world moves. The lab appends this
plugin after the run's other Controls.

See [Environment](../../../../../../../docs/manifests/environment.md).
