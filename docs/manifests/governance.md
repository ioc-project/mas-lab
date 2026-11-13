<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Governance (`spec.governance`)

Live `spec.governance` is a set of **governance** plugins on a [Control](control.md). A plugin is a class that a library registers and a manifest names. Each governance plugin can allow, block, modify, or ask a human before a tool call or LLM call runs. It sees the result or error again when it comes back.
Write a set to stop undeclared or destructive tool calls. Also write one to require human in the loop (HITL) approval, or to set a recovery policy for engine errors.
Leftover Agent `spec.governance` is deprecated and still applied when no matching Control names governance. This page is not a YAML kind.

Minimal working chain. It blocks any tool the LLM call did not declare:

```yaml
spec:
  governance:
    - gov_no_undeclared_tool
```

- Entry syntax: [plugin-bindings.md](plugin-bindings.md).
- Schema: [`governance-binding.schema.yaml`](../schemas/runtime/fragments/governance-binding.schema.yaml).
- Developer contract: [runtime/docs/dev/contracts/governance.md](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/dev/contracts/governance.md).
- Retries, circuit breaker, `error_policy` defaults, and logging: [reliability.md](../references/reliability.md).

---

## Chain

`spec.governance` is an **iptables-style chain**, not a list order. Plugins that attach to the same transition run in `priority` order (higher first, default 0; same-priority order is the plugin id). BLOCK stops the chain and returns that error. ALLOW passes control to the next plugin.
`spec.observability` differs: it is a sequence, and every plugin always runs.

Two hook points carry governance decisions:

- **Egress** runs before a call leaves the agent (`TOOL_CALL`, `LLM_CALL`). The egress chain can allow, block, or ask a human.
- **Ingress** runs when a result or an engine `ERROR` comes back, before it enters the agent's working memory.

| Layer | When | Who |
|-------|------|-----|
| Infra retry | Inside the LLM HTTP POST or tool invocation | Agent `spec.control.retry` (deprecated, still applied) |
| Circuit breaker | After infra retries on `unavailable` | Agent `spec.control.circuit_breaker` (deprecated, still applied) |
| Egress chain | Before the engine call (`TOOL_CALL`, `LLM_CALL`) | Plugins in `spec.governance[]` |
| Ingress `error_policy` | After `_end`, on engine `ERROR` | `retry_on_error` plugin, if installed |

Do not put retry budgets on the egress chain. Infra retry re-issues the same call. Ingress `error_policy.transient: retry` schedules a new envelope step, up to `max_gov_retries`. Engine retry defaults: [reliability.md](../references/reliability.md#defaults-bare-agent-no-overlay).

Without `error_recovery_plugin`, an `ERROR` follows `gov_ingress_profile` (default `PERMISSIVE`, which allows). The kernel does not interpret `error_policy`: it is the plugin's config. The recovery plugin is installed on the ingress chain. It is not a kernel slot.

---

## Shipped plugins

| Plugin | Role | Card |
|--------|------|------|
| `gov_no_undeclared_tool` | Egress BLOCK if the tool name was not in this LLM call's `tools` list. | [no-undeclared-tool.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/no-undeclared-tool.md) |
| `gov_no_undeclared_peer` | Egress BLOCK if leftover `delegate_to_*` names a dest not in this agent's `delegates_to`. | [no-undeclared-peer.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/no-undeclared-peer.md) |
| `retry_on_error` | Ingress: `error_policy.<class>` maps to allow, retry, block, or skip. Egress always passes. | [retry-on-error.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/retry-on-error.md) |
| `backtrack_on_error` | Ingress: retries an identical engine error, then requests a session rollback once the repeat threshold is reached. Configure it with a `backtrack` stanza key. | [governance README](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/README.md) |
| `sample_governance` (alias of `sample`) | HITL before a tool call or before a tool result enters working memory. Also provides destructive-tool profiles. | [sample-governance.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/sample-governance.md) |

Any plugin registered as `type: governance` in a library manifest can join the chain. The schema checks only the shape (a bare id or a single-key map). An unknown id fails when the agent is built.

The default chain is empty (`[]`).

### Error recovery

`error_recovery_plugin` and `error_policy` can sit on any stanza, like `hitl_on_tool`. The parser flattens them and installs the named plugin on the ingress chain. The kernel never stores a plugin instance of its own.

```yaml
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
```

The `with-hardened` Control sets this up for you:

1. It names `gov_no_undeclared_tool`, `gov_no_undeclared_peer`, `budget` and `retry_on_error` in `spec.governance`.
2. The `budget` plugin is set to `max_llm_calls: 50` and `max_tool_calls: 100`.
3. Infra retry defaults to 4 LLM and 2 tool attempts. The circuit breaker is `standard:llm-proxy-hardened` (and `standard:tool-server-hardened`), not this Control.

Details: [reliability.md](../references/reliability.md#with-hardened).

### Trace events

Each ingress or egress decision is a `kind: governance_decision` record in native `events.jsonl`. Typed failures also carry `failure_class`, `failure_code`, and `retry_attempts`. Infra re-attempts are logs on `mas.runtime.reliability`, not governance events. See [reliability.md § Logging](../references/reliability.md#logging).

---

## Stanza keys read by the runtime

Besides the plugin's own constructor arguments, the runtime reads these keys from any stanza in the chain. Each key applies to the whole agent.
When two Controls name the same plugin id, the later Control's config wins. When two stanzas in one parsed set set the same kernel field, the first in priority order wins.

| Key | Default | Effect |
|-----|---------|--------|
| `hitl_on_tool` | `false` | Ask a human before a tool call runs (egress). |
| `hitl_on_tool_result` | `false` | Ask a human before a tool result enters working memory (ingress). |
| `hitl_once_per_turn` | `false` | After one tool call is approved in a turn, later tool calls in that turn skip the prompt. |
| `hitl_mode` | `interactive` | Who answers HITL prompts: `interactive` (terminal operator), `auto_approve`, or `auto_deny`. A non-interactive session with `interactive` auto_approves. |
| `gov_policy_profile` | `BLOCK_DESTRUCTIVE` | Built-in policy for destructive tools: `PERMISSIVE`, `BLOCK_DESTRUCTIVE`, `HITL_DESTRUCTIVE`, `LOG_ALL`, `MODIFY_DESTRUCTIVE`, `TERMINATE_DESTRUCTIVE`, `SKIP_DESTRUCTIVE`, `BLACKLIST_DESTRUCTIVE`. |
| `gov_block_destructive` / `gov_trigger_destructive` | `false` | Turn the destructive-tool policy on. |
| `gov_ingress_profile` | `PERMISSIVE` | Ingress decision on engine `ERROR` when no recovery plugin is installed: `PERMISSIVE`, `RETRY_ON_ERROR`, `BLOCK_ON_ERROR`, `SKIP_ON_ERROR`. |
| `max_gov_retries` | `2` | Cap on governance-requested retries of one step. |
| `error_recovery_plugin`, `error_policy` | unset | Ingress recovery plugin and its policy table (see [Error recovery](#error-recovery)). |
| `backtrack` | unset | Config for `backtrack_on_error`: `repeat_threshold`, `max_backtracks_per_session`, `steps` (integers >= 1) and `on_cap_reached` (`hitl`, `terminate`, `stop_backtracking`). |

If no governance plugin is named but `hitl_on_tool`, `hitl_on_tool_result`, or `gov_trigger_destructive` is set, the runtime installs `sample_governance` with those flags.

Smallest HITL chain. A terminal operator approves each tool call:

```yaml
governance:
  - sample_governance:
      hitl_on_tool: true
```

Egress `BLOCK` on a tool call writes the policy reason as that call's tool
result and returns the agent to evaluate. The next model call is its own
envelope; it is not started from inside the blocked tool. In a parallel batch
each call is authorized on its own: a denied sibling goes back to the model as
its result, and the allowed calls still run. HITL and `TERMINATE` still stop
the batch.

`hitl_mode: auto_deny` refuses the tool and ends the turn with that denial as
the user-visible answer. Optional steering is appended to the denial. `SKIP`
injects optional steering as the tool result and lets the model continue.
Walkthrough: [Tutorial 14](../tutorials/14-governance-hitl/README.md).

## See also

- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
- [Agent manifest](agent.md): where `spec.governance` lives
- [Plugin bindings](plugin-bindings.md): entry syntax
- [Control manifest](control.md): attach governance to an existing agent
