<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# circuit_breaker — call layer

| Field | Value |
|-------|--------|
| **ID** | `circuit_breaker` (alias `threshold`) |
| **Type** | `layer` |
| **URN** | `mas.circuit_breaker.threshold` |
| **Implementation** | `CircuitBreakerLayer` in `mas.library.standard.plugins.layers.circuit_breaker` (rules, state and probe all live there) |
| **Applies to** | `llm`, `tool` (priority 60: inside `retry`, outside the scheduler) |
| **Manifest** | a `circuit_breaker` step on the infra pipeline; `spec.control.circuit_breaker` still works (deprecated) |
| **Bundles** | `standard:llm-proxy-hardened` (`breaker-llm`), `standard:tool-server-hardened` (`breaker-tool`) |

Fails fast after consecutive `unavailable` (default) failures on the same target (`llm:<api base>` or
`tool:<name>`), then lets one probe through after `reset_timeout_s`. A tool call whose tool is not known gets
no breaker rather than sharing one with every other nameless call.

## State lives in the running spec

The breaker keeps what it learns in the session's **running spec** (the copy of the agent spec made at
start, which then evolves), under `spec.layer_state.circuit_breaker.<target>`:

```yaml
layer_state:
  circuit_breaker:
    "tool:search": {failures: 5, opened_at: 1760000000.0}
```

The layer never writes the spec itself. The session gives it a control handle limited to `layer_state`,
`swap_layer_state`, `disable_tool` and `enable_tool`, and proposes each change as a `spec_delta`. The session's
spec machine (`mas.runtime.session.spec_machine`) decides it: `apply`, `busy` while the session
checkpoints or is over, or `conflict` when the state is no longer the one the breaker read. The machine is
the only thing that orders writes, so there is no lock and no guard in the plugin: on `conflict` the shared
helper (`mas.runtime.layers.state.transition`) reads again and proposes again. Applied changes are traced
(`spec_revised`, actor `layers`) and restored by a snapshot or what-if restore. A healthy circuit writes
nothing. Outside a managed session there is no running spec, so the layer keeps the state in memory.

## Taking a tool away and bringing it back

A tool that declares a `probe` (a safe, repeatable call; put it on the Tool manifest or inline entry) is
taken out of the running spec when its circuit opens, so the model stops offering it:

```yaml
tools:
  - name: search
    probe: {arguments: {query: ping}}
```

The model cannot call a tool that is gone, so the breaker probes it itself: once `reset_timeout_s` has passed,
at the next LLM call, it runs the probe. A probe that succeeds closes the circuit and puts the tool back; one
that fails opens it again for another `reset_timeout_s`. The breaker puts back only what it took away (the spec
keeps `disabled_by: circuit_breaker`); a tool an operator disabled stays disabled. A tool with no `probe`
stays offered and its calls just fail fast.

## Pipeline

```yaml
pipeline:
  - id: breaker-llm
    layer: circuit_breaker
    for: [llm]
    per: destination
    params:
      failure_threshold: 5
      reset_timeout_s: 30
      "on": [unavailable]
```

| Param | Default |
|-------|---------|
| `enabled` | `true` |
| `failure_threshold` | `5` |
| `reset_timeout_s` | `30` |
| `"on"` | `[unavailable]` — **quote the key** in YAML |

Params are checked when the layer is built, so a bad one stops the run at start.

## Testing it against another setting

Each step has an `id` (`retry-llm`, `retry-tool`, `breaker-llm`, `breaker-tool` in the shipped defaults and
bundles; otherwise `<layer>-<for>`). An overlay or an experiment addresses one step by it and leaves the rest:

```yaml
pipeline:
  - id: breaker-llm
    enabled: false          # arm B: no breaker; arm A keeps it
  - id: retry-llm
    params: {max_attempts: 1}
```

Reference: [reliability.md](../../../../../../../docs/references/reliability.md).
