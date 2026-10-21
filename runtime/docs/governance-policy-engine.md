<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Governance policy engine

The policy engine lets you write governance rules (guardrails, human-in-the-loop approval, blocking, blacklisting) in YAML, without writing a plugin.
It is for agent authors who need to restrict tool calls.

- **Module:** `mas.runtime.boundary.gov.policy_engine` (`GovernancePolicyEngine`)
- **Declared in:** the `policies:` list of a `spec.governance` plugin stanza
- **Decides at:** the egress chokepoint of the kernel envelope

Block any call to `flight_search` whose destination is in a list:

```yaml
spec:
  governance:
    - sample_governance:
        policies:
          - name: forbidden-destination
            trigger: {on: tool_input, tool: flight_search, condition: "arguments.destination in ['NK', 'SY']"}
            action: block
```

---

## 1. Overview

Each rule has three independent parts:

```text
trigger  x  evaluation  x  action
```

- **trigger**: where the rule applies (`on`) and which tool it concerns (`tool`)
- **evaluation**: how the condition is checked (`deterministic`)
- **action**: what happens when the rule fires (`block`, `hitl`, ...)

The engine parses rules when the agent is built (`GovernancePolicyEngine.from_yaml`). A malformed rule raises `PolicyParseError` naming the policy. At egress, the kernel asks the engine for the first matching rule. If none matches, the governance profile of the plugin chain decides (see
[mealy-envelope.md](mealy-envelope.md) and [plugin-and-tool-authoring.md](plugin-and-tool-authoring.md#7-governance-plugins)).

## 2. Configuration

Policies live in a governance plugin stanza of the agent manifest or an overlay:

```yaml
spec:
  governance:
    - sample_governance:
        policies:
          - name: forbidden-destination
            trigger:
              on: tool_input
              tool: flight_search
              condition: "arguments.destination in ['NK', 'SY']"
            action: block
          - name: human-review
            trigger:
              on: tool_input
              tool: book_flight
              condition: "arguments.price > 500"
            action: hitl
            params: {auto_approve: false, timeout_s: 30}
```

(Quote the key `"on"` in YAML 1.1 parsers that read bare `on` as a boolean.)

`policies` lists from several stanzas or stacked overlays are concatenated, in declaration order.

### Policy fields

```yaml
- name: <string>           # Required. Policy identifier.
  enabled: <bool>          # Optional (default true). false skips the policy.
  trigger:
    on: <trigger_point>    # Required.
    tool: <string>         # Optional (default "*"): tool name filter.
    condition: <string>    # Optional (default ""): condition expression.
    evaluation: <mode>     # Optional (default "deterministic").
    threshold: <int>       # tool_failure_streak only (default 3).
    reset_on: <string>     # tool_failure_streak only (default "success").
  action: <action>         # Required.
  params: <dict>           # Optional. Action parameters (for example message / reason).
  recovery: <dict>         # Optional.
```

### Trigger points (`on:`)

| Value | Meaning |
|-------|---------|
| `tool_input` | Before a tool executes; the condition sees `arguments.*` |
| `tool_output` | After a tool returns |
| `llm_output` | After the model responds |
| `delegation_output` | After a delegated agent returns |
| `budget_threshold` | After a model call, against cumulative usage |
| `event` | Meta-policy on governance events |
| `tool_failure_streak` | After `threshold` consecutive failures of a tool |

`tool_failure_streak` counts consecutive failures per tool (`record_tool_failure`); a success clears the count (`record_tool_success`). `blacklist` adds the tool to `tool_blacklist`, checked by `is_blacklisted`.

The parser accepts all the values above. In this release the kernel's egress gate evaluates only **`tool_input`** policies, when it authorizes a `TOOL_CALL`. Use `tool_input` for rules that must be enforced today, and use plugins from `library-standard` (budget, `retry_on_error`, circuit breaker) for the other concerns; see [docs/manifests/governance.md](../../docs/manifests/governance.md).

### Condition expressions

For `deterministic` evaluation a condition is `<path> <operator> <literal>`:

- **Paths**: dotted access into the data dict, for example `arguments.destination`.
- **Operators**: `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not in`, `contains`.
- **Literals**: numbers, quoted strings, `True`/`False`, `None`, lists such as `['NK', 'SY']`.
- An empty condition always fires. A condition that does not parse never fires.

### Evaluation modes

The engine evaluates only `deterministic`. The parser accepts any other `evaluation` value (for example `llm_judge`), but such a policy never fires.

### Actions

| Action | Effect |
|--------|--------|
| `block` | Refuse the call; the reason is returned to the model (recoverable) |
| `terminate` | Refuse the call and end the run (not recoverable) |
| `hitl` | Ask a human to approve or deny before the call proceeds |
| `skip` | Skip the call |
| `retry` | Retry the call |
| `blacklist` | Skip the call and every later call to the same tool |
| `log` | Record the decision and allow the call |
| `modify` | Allow the call with a modified payload |

`params.message` (or `params.reason`) is used as the human-readable reason in the decision.

## 3. Decisions and events

Each egress decision is a `governance_decision` event in `events.jsonl`, with the policy name, decision, reason, and the `hook` and `checkpoint` it applied to; see [trajectory-schema.md](trajectory-schema.md). A HITL decision raises a `hitl_request`. The session resolves it through `mas-ctl chat` or `tui` (interactive) or the auto-approve responder (batch).

## 4. Testing

```python
from mas.runtime.boundary.gov.policy_engine import ConditionEvaluator, GovernancePolicyEngine

assert ConditionEvaluator.evaluate("x > 5", {"x": 10})
assert not ConditionEvaluator.evaluate("x > 5", {"x": 3})
assert ConditionEvaluator.evaluate("items contains \"a\"", {"items": ["a", "b"]})

engine = GovernancePolicyEngine.from_yaml({"policies": [{
    "name": "cap",
    "trigger": {"on": "tool_input", "tool": "calc", "condition": "arguments.value > 100"},
    "action": "block",
}]})

policy = engine.find_matching_policy("tool_input", {"arguments": {"value": 200}}, tool_name="calc")
assert policy is not None and policy.name == "cap"
assert engine.find_matching_policy("tool_input", {"arguments": {"value": 50}}, tool_name="calc") is None
```

Runtime tests live in `runtime/tests/test_gov_policy.py` and `runtime/tests/test_gov_spec.py`.

## 5. Design principles

- **Orthogonal axes**: any trigger can use any evaluation and any action.
- **Fail-safe parsing**: the build rejects unknown trigger points and actions, and an unparsable condition does not fire.
- **No coupling to tools**: rules reference tools by name only.
- **Declaration order**: the first matching rule wins.
