<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Reliability: retries, circuit breaker, error policy

This page covers what happens when an LLM call or a tool call fails: retries, the circuit breaker, `error_policy`, defaults, and logging. Read it when a run stops on an error you expect to recover from.

## The problem

Calls fail for different reasons. A dropped socket is worth a retry. A bad API key is not. The runtime sorts each failure into one of four *failure classes*, then applies one rule per layer.

Example: the infra layer retries a dropped socket. A tool `ValueError` goes back to the model as a tool result.

This is a runtime topic, not a YAML kind. Agent field lists are in [agent.md](../manifests/agent.md). The governance chain and its plugins are in [governance.md](../manifests/governance.md). Infra retry is a pipeline layer ([infra.md](../manifests/infra.md#llm-retry)).

Terms: *infra* is the layer that says where things are and how to reach them (LLM endpoint, tool servers). *Governance* is the policy layer that approves, blocks, or retries calls. A *plugin* is a unit of behaviour that a manifest selects by name. A *flavour* is the glue that links the logic spec to the infra (its `bind`). A *run* is one execution of an agent or MAS.

## Layers

Two layers share one taxonomy: `transient`, `unavailable`, `application`, `fatal`. Infra retry, the circuit breaker, and ingress governance therefore agree on *what happened*. A third layer, the lab, retries whole runs.

| Layer | Where | What it does |
|-------|--------|----------------|
| **Infra** | retry layer around llm and tool calls | Re-issues the same call (`RetryPolicy`). After the budget is spent, an optional **circuit breaker** layer acts on `unavailable`. |
| **Governance** | Ingress, after the call returns (the `_end` hook) | `error_policy` maps the typed failure onto `allow`, `retry`, `block`, or `skip`. Plugin: `retry_on_error`. |
| **Lab** | `mas-lab benchmark run` | Re-issues a failed scenario, item, and run (`schedule.max_attempts`, default 3). Independent of `n_runs`. |

- Application tool errors stay a `TOOL_RESULT` (working memory).
- Exhausted LLM failures stay `ERROR`.
- Unknown tool names are `application` / `TOOL_UNKNOWN`. They are not circuit-breaker `unavailable`.

Schemas: [`control-binding.schema.yaml`](../schemas/runtime/fragments/control-binding.schema.yaml), [`retry-policy.schema.yaml`](../schemas/runtime/fragments/retry-policy.schema.yaml).

Code:

- Engine helpers: `mas.runtime.reliability` (taxonomy, classify, infra retry).
- LLM HTTP POST: the `llm_provider` plugin (`mas.library.standard.plugins.llm.http`); retry wraps that call, it is not a loop inside the client.
- Circuit breaker layer: `mas.library.standard.plugins.layers.circuit_breaker`; its rules, state and probe all live in that one plugin. Its state lives in the running spec (`spec.layer_state`), changed only through the session's control handle and decided by its spec machine (no lock in the plugin); a tool that declares a `probe` is taken out of the spec while its circuit is open and put back when the probe passes.
- Logger: `mas.runtime.reliability`.

---

## Defaults (bare agent, no overlay)

For ordinary chat and LLM HTTP, the runtime retries a dropped socket, connect-refused, or HTTP 500. It shows a tool `ValueError` to the model. A bad TLS setup stops the turn. A dropped keep-alive socket also drops the pooled HTTP client so the next try opens a new connection. A streamed response is not retried after a chunk has already been shown.

| Knob | Default |
|------|---------|
| LLM `max_attempts` | `4` (documented default pipeline) |
| LLM `retry_on` | `[transient, unavailable]` |
| LLM `backoff_s` / `backoff_multiplier` / `jitter` | `0.5` / `2.0` / `true` |
| LLM `require_idempotent` | `false` (HTTP POST of the same payload is treated as idempotent) |
| LLM `max_backoff_s` | unlimited |
| Tools `max_attempts` | `2` |
| Tools `retry_on` | `[transient, unavailable]` |
| Tools `require_idempotent` | `true` (transient retries only if the tool is idempotent: Tool YAML `spec.idempotent: true`, or `ToolContract.is_idempotent()`) |
| Circuit breaker | **off** (omit `spec.control.circuit_breaker`, or set `null`) |
| Ingress (no recovery plugin) | `gov_ingress_profile` **PERMISSIVE** (ALLOW engine ERROR) |
| `error_policy.*` | **unset** until `retry_on_error` is installed |
| `error_recovery_plugin` | unset |
| `max_gov_retries` | `2` |
| Lab run `max_attempts` | `3` (`experiment.schedule.max_attempts`; set `1` to disable) |
| `budget` governance plugin | not named (no call-count ceiling) |

### `with-hardened`

One Control, written once and attached to every agent you deploy: `pkg://mas.library.standard/controls/with-hardened.yaml`.

| Knob | Value |
|------|--------|
| Egress | `gov_no_undeclared_tool`, `gov_no_undeclared_peer` |
| Call ceilings | the `budget` plugin: `max_llm_calls: 50`; `max_tool_calls: 100` |
| Ingress | `retry_on_error` with `error_recovery_plugin: retry_on_error` |
| `error_policy` | transient/unavailable → `retry`; application → `allow`; fatal → `block` |

```bash
mas-ctl chat agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml \
  --control pkg://mas.library.standard/controls/observability-native.yaml
```

Two production settings are not governance, so they are not in it. The infra retry already defaults to 4 LLM and 2
tool attempts (documented in `runtime/layers/defaults.yaml`). The circuit breaker is off unless the infra pipeline lists it.
`standard:llm-proxy-hardened` (and `standard:tool-server-hardened`) turn the breaker on for those leaves:
`failure_threshold` 5, `reset_timeout_s` 30, `on: [unavailable]`, with retry outside the breaker. The dispatch-loop
cap is `design_pattern.params.max_steps` (or a RuntimeEngine manifest). `with-hardened` was an overlay that
patched the agent spec; it is now a Control for call ceilings and error policy.

### Testing one setting against another

Every retry or breaker step around an llm, tool, or send call has a name (`retry-llm`, `retry-tool`, `breaker-llm`,
`breaker-tool` in the defaults and the hardened bundles; otherwise `<layer>-<for>`). An overlay, or an experiment
factor, addresses one step by that name and leaves the others alone. `enabled: false` switches a step off without
removing it, and for `retry` it also keeps the documented default from coming back:

```yaml
kind: Overlay
spec:
  target: {kind: Infra}
  patch:
    pipeline:
      - id: breaker-llm
        enabled: false             # arm B: no breaker
      - id: retry-llm
        params: {max_attempts: 1}  # and one attempt
```

`mas-ctl infra resolved` prints each step with its `id` and marks a disabled one, so the arm that ran is on record.

---

## Failure classes

| Class | Meaning | Infra default | Ingress default |
|-------|---------|---------------|-----------------|
| `transient` | Peer answered or the socket dropped; a second identical call may work (429, 5xx including 500, timeout, reset). | LLM retries; tools retry only if idempotent | `allow` (`retry` on `with-hardened`) |
| `unavailable` | Peer is not there (connect refused, DNS, missing provider, open circuit). | LLM and tools retry (LLM POST is treated as idempotent) | `allow` (`retry` on `with-hardened`) |
| `application` | Call ran and returned a domain error (4xx body, tool `ValueError`, unknown tool name). | Never retried | `allow` (store in WM) |
| `fatal` | Will not heal without a config change (TLS verify, 401/403). | Never retried | `block` |

To make a provider trip the breaker, declare a tool as down with `ExplicitToolUnavailableError`.

---

## Spec knobs

### `spec.control.retry.llm` / `.tools`

`spec.control` is deprecated: `retry` and `circuit_breaker` still apply (one warning) and become pipeline steps. Prefer a `retry` / `circuit_breaker` layer on the infra pipeline (next to the LLM cache, scheduler and fault injection). Its `budget` and `rate_limiter` keys are read by nothing; call ceilings are the `budget` governance plugin.

| Field | Type | LLM default | Tools default |
|-------|------|-------------|----------------|
| `max_attempts` | integer ≥ 1 | `4` | `2` |
| `backoff_s` | number ≥ 0 | `0.5` | `0.5` |
| `backoff_multiplier` | number ≥ 1 | `2.0` | `2.0` |
| `jitter` | boolean | `true` | `true` |
| `retry_on` | list of failure classes | `[transient, unavailable]` | `[transient, unavailable]` |
| `require_idempotent` | boolean | `false` | `true` |
| `max_backoff_s` | number ≥ 0 or omit | unlimited | unlimited |

`spec.control.retry.llm` becomes a pipeline step. Prefer a `retry` layer on the infra pipeline.

### `spec.control.circuit_breaker`

Omit the key (or set `null`) to leave the breaker off. If the key is present, it becomes a `circuit_breaker` pipeline step (default `threshold`). Prefer `standard:llm-proxy-hardened`. It is not a kernel operation and not a governance plugin.

Card: [circuit-breaker.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/layers/circuit-breaker.md).

| Field | Type | Default when present |
|-------|------|----------------------|
| `plugin` | string | `threshold` |
| `enabled` | boolean | `true` |
| `failure_threshold` | integer ≥ 1 | `5` |
| `reset_timeout_s` | number ≥ 0 | `30` |
| `"on"` | list of failure classes | `[unavailable]`. **Quote the key** in YAML: bare `on` is boolean true. |

### `spec.governance[].error_policy` + `error_recovery_plugin`

These YAML fields are flattened from any plugin stanza. The runtime installs the named plugin on the ingress chain. The kernel only applies the resulting `GovernanceAction`. Chain semantics are in [governance.md](../manifests/governance.md).

| Field | Values | Default |
|-------|--------|---------|
| `error_policy.transient` | `allow` `retry` `block` `skip` | `allow` |
| `error_policy.unavailable` | same | `allow` |
| `error_policy.application` | same | `allow` |
| `error_policy.fatal` | same | `block` |
| `error_recovery_plugin` | plugin id | unset (ingress follows `gov_ingress_profile`) |

Ingress `retry` re-issues the scheduled LLM or tool call up to
`max_gov_retries` (default 2).

### Environment (last resort, interactive sessions only)

| Variable | Effect |
|----------|--------|
| `MAS_LLM_HTTP_RETRIES` | Extra LLM HTTP retries (`attempts = retries + 1`). Default `3`. |
| `MAS_LLM_HTTP_RETRY_BACKOFF` | Base backoff seconds (minimum `0.05`); also disables jitter. |

ctl reads these for one interactive session and passes the values down. The runtime does not read them. They fill keys the pipeline retry did not set. An explicit pipeline param wins. Do not put them in `.env` profiles or in a spec. Prefer a `retry` layer on the infra pipeline. See [user-config.md](../user-config.md).

---

## Logging

The logger name is **`mas.runtime.reliability`**. Filter JSON logs on `mas.reliability=true`.

| Outcome (`mas.outcome`) | Level | When |
|-------------------------|-------|------|
| `retry` | WARNING | Infra will sleep and re-issue the same call |
| `recovered` | INFO | Call succeeded after at least one retry |
| `no_retry` | WARNING | Classified failure that the policy will not retry |
| `exhausted` | ERROR | Retry budget spent |
| `circuit_open` / `circuit_half_open` / `circuit_closed` | WARNING / INFO | Breaker state change |
| `llm_error` | ERROR | Engine returns `ERROR` after provider retries |
| `ingress_retry` / `ingress_block` / `ingress_skip` | WARNING / ERROR | Ingress `error_policy` decision |

Each record also carries `mas.target`, `mas.failure_class`, `mas.failure_code`, `mas.attempt`, `mas.max_attempts`, and `mas.delay_s` when they apply.

Native `events.jsonl` `governance_decision` rows for ingress include `failure_class`, `failure_code`, and `retry_attempts` when the engine set them. Infra re-attempts appear in logs only. They happen in the retry layer around the provider call, before `_end`.

---

## Where reliability behaviour goes

A flavour is the glue between the logic spec and the infra (`bind`). It is not a dump of every agent plugin. Behaviour that changes the trajectory belongs on Agent `governance[]` and `control`, usually through stacked overlays. See [governance.md](../manifests/governance.md) and [flavour.md](../manifests/flavour.md).

| Concern | Overlay / plugin | Notes |
|---------|------------------|--------|
| Undeclared tools | `with-hardened` → `gov_no_undeclared_tool` | Egress chain BLOCK |
| Leftover send dest | `with-hardened` → `gov_no_undeclared_peer` | Egress chain BLOCK when dest is not in `delegates_to` |
| Typed retry / breaker | `with-hardened` → `retry_on_error` (Control); infra retry defaults and `standard:llm-proxy-hardened` for the breaker | Infra then ingress |
| Native traces | `observability-native` | Sequence, not chain |
| Checkpoints / resume | separate overlay | Compose it. The flavour does not carry it. |
| Backtrack on application errors | `error_policy.application` or a dedicated plugin | The default is `allow` (stored in working memory). |
| HITL | `sample_governance` stanza on the chain | The flavour should not own HITL (human in the loop) |

---

## Full example

```yaml
spec:
  tools:
    # The Tool YAML sets spec.idempotent: true so transient retries apply.
    - ref: ./tools/get_metrics.tool.yaml
  governance:
    - gov_no_undeclared_tool
    - gov_no_undeclared_peer
    - retry_on_error:
        error_recovery_plugin: retry_on_error
        error_policy:
          transient: retry
          unavailable: retry
          application: allow
          fatal: block
  control:
    retry:
      llm:
        max_attempts: 4
        retry_on: [transient, unavailable]
      tools:
        max_attempts: 2
        retry_on: [transient, unavailable]
        require_idempotent: true
    circuit_breaker:
      failure_threshold: 5
      reset_timeout_s: 30
      "on": [unavailable]
```

## See also

- [Governance](../manifests/governance.md)
- [Agent manifest](../manifests/agent.md)
- [Infra](../manifests/infra.md)
- [Tutorial 11: sessions and recovery](../tutorials/11-sessions-and-recovery/README.md)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
