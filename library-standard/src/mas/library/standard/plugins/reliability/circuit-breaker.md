<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# circuit_breaker — engine I/O wrapper

| Field | Value |
|-------|--------|
| **ID** | `circuit_breaker@v1` |
| **Alias** | `circuit_breaker`, `threshold` |
| **Kind** | library (`circuit_breaker`) — not a boundary slot |
| **URN** | `mas.circuit_breaker.threshold` |
| **Implementation** | `ThresholdCircuitBreaker` in `mas.library.standard.plugins.reliability.circuit_breaker` |
| **Manifest keys** | `spec.control.circuit_breaker` |
| **Control** | `pkg://mas.library.standard/controls/with-hardened.yaml` |

Fail-fast after consecutive `unavailable` (default) failures on the same
LLM or tool target. The engine consults this plugin **inside** execute,
after infra retries, before `_end`. The kernel never sees breaker state.

This is **not** a governance plugin. Ingress `retry_on_error` decides
what to do once the envelope already has a typed failure.
[reliability.md](../../../../../../../docs/references/reliability.md).

## Manifest

Omit the key (or set `null`) to leave the breaker off. Presence loads
this plugin (swap with `plugin:` if another implementation is registered).

```yaml
control:
  circuit_breaker:
    failure_threshold: 5
    reset_timeout_s: 30
    "on": [unavailable]
```

| Field | Default when present |
|-------|----------------------|
| `plugin` | `threshold` |
| `enabled` | `true` |
| `failure_threshold` | `5` |
| `reset_timeout_s` | `30` |
| `"on"` | `[unavailable]` — **quote the key** in YAML |

Overlay index: [../../overlays/README.md](../../overlays/README.md).
