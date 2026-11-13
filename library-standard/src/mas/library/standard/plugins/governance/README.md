<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Governance / HITL operator plugins (HitlResponder contract)

Manifest `spec.governance.hitl_mode` selects the operator plugin at ctl bootstrap.

| Plugin id | Contract | Wired at | Use |
|-----------|----------|----------|-----|
| `hitl-auto-approve@v1` | `HitlResponder` | `KernelDriver.hitl` | CI, batch, `-q` runs |
| `hitl-auto-deny@v1` | `HitlResponder` | `KernelDriver.hitl` | Negative tests |
| `hitl-interactive@ctl` | `HitlTerminal` | ctl session boundary | TTY / TUI operator |
| `gov_no_undeclared_tool` | `GovernancePlugin` | `spec.governance` chain | BLOCK tool names not in this LLM call's `tools` list (pass otherwise); reason is fed back as a tool observation. Example: [examples/governance/undeclared-tool/](../../../../../../examples/governance/undeclared-tool/). Overlay: `with-hardened`. |
| `gov_no_undeclared_peer` | `GovernancePlugin` | `spec.governance` chain | BLOCK leftover `delegate_to_*` dest not in this agent's `delegates_to` (pass otherwise). Overlay: `with-hardened`. |
| `retry_on_error` | `ErrorRecoveryPlugin` | ingress chain via `spec.governance[].error_recovery_plugin` | Map engine `failure_class` through `error_policy`. Kernel only applies the decision. Card: [retry-on-error.md](retry-on-error.md). Overlay: `with-hardened`. |
| `backtrack_on_error` | `ErrorRecoveryPlugin` | ingress chain via `spec.governance[].error_recovery_plugin` | Retry an identical engine error, then request session-level rollback at the configured repeat threshold. Overlay: `openclaw`. |

Retry and circuit_breaker stay on Agent `spec.control` (deprecated, still applied). They are not Control plugins.
The circuit breaker plugin (`threshold`) is loaded from that agent key. Card: [circuit-breaker.md](../layers/circuit-breaker.md).

Plugin cards: [no-undeclared-tool.md](no-undeclared-tool.md), [no-undeclared-peer.md](no-undeclared-peer.md), [retry-on-error.md](retry-on-error.md).
Overlay index: [../../overlays/README.md](../../overlays/README.md).

Ingress-only governance plugins may be selected under
`spec.governance[].ingress_plugins`; their implementations resolve through the
same `governance` PluginRegistry category and receive an ingress intent, not a
kernel handle.

Kernel emits `EmitHitlRequest` only from `M_gov` egress gate. Only `M_gov` enters `HITL_PENDING`.
`M_tool` enters `WAIT_GOV`; `M_dp` stays `AWAITING_INGRESS` (waiting on the tool chain).
