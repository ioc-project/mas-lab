<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# retry_on_error — ErrorRecoveryPlugin

| Field | Value |
|-------|--------|
| **ID** | `retry_on_error@v1` |
| **Alias** | `retry_on_error` |
| **Kind** | governance |
| **URN** | `mas.gov.retry_on_error` |
| **Implementation** | `RetryOnErrorPlugin` in `mas.library.standard.plugins.governance.retry_on_error` |
| **Manifest keys** | `spec.governance` chain + `error_recovery_plugin` / `error_policy` (ingress chain, not a kernel slot) |
| **Control** | `pkg://mas.library.standard/controls/with-hardened.yaml` |

Classifies an engine `ERROR` (or a `TOOL_RESULT` that already carries
`failure_class`) after infra retries have finished. The mapping is
`error_policy.<class>` → `allow` / `retry` / `block` / `skip`. Egress
always PASSes, so this plugin can sit on the same chain as
`gov_no_undeclared_tool`.

Retry and circuit_breaker stay on Agent `spec.control` (deprecated, still applied). They are not Control plugins.
This plugin is the *governance* half: what to do once the envelope sees a
typed failure. Card: [circuit-breaker.md](../layers/circuit-breaker.md). Full reference (defaults, knobs, logging):
[reliability.md](../../../../../../../docs/references/reliability.md).
Chain: [governance.md](../../../../../../../docs/manifests/governance.md).

## Manifest

```yaml
plugins:
  - retry_on_error:
      error_recovery_plugin: retry_on_error
      error_policy:
        transient: retry
        unavailable: retry
        application: allow   # store in working memory; do not re-issue
        fatal: block
  - retry:
      llm:
        max_attempts: 4
        retry_on: [transient, unavailable]
      tools:
        max_attempts: 2
        retry_on: [transient, unavailable]
        require_idempotent: true
  - circuit_breaker:
      failure_threshold: 5
      reset_timeout_s: 30
      "on": [unavailable]
```

Or apply the standard overlay (appends the undeclared-tool rule, this
plugin, hardened retry/circuit knobs):

```bash
mas-ctl chat agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml
```

| Attribute | Meaning |
|-----------|---------|
| `error_recovery_plugin` | YAML sugar (flattened from any stanza). Set to `retry_on_error` so that plugin is installed on the ingress chain. |
| `error_policy` | Per-class ingress action. Defaults: transient/unavailable/application `allow`, fatal `block`. |

Overlay index: [../../overlays/README.md](../../overlays/README.md).
