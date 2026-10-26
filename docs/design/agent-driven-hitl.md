<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent-Driven HITL via System Tools

Status: Implemented as a blocking system tool. The non-blocking "pause the
turn and resume later" variant under [Not implemented](#not-implemented) is
still a proposal.

## Problem

HITL means human in the loop: a person answers or approves. Governance-triggered HITL (`hitl_on_tool`, `hitl_on_tool_result`) is decided by policy, not by the agent. *Governance* is the policy layer that approves or blocks calls. Two needs are not covered:

1. **Agent-initiated user interaction.** An agent should be able to ask the
   user a question explicitly and use the answer within its own turn.
2. **Delegation and distribution.** The session that hosts the agent resolves governance HITL locally. When the asking agent is a delegate, the question must still reach whoever talks to the user. The delegation return path carries only text.

## Design

### System tool: `request_human_input`

A built-in system tool, not an app-local plugin. Each agent opts in:

```yaml
spec:
  tools:
    - kind: system
      name: request_human_input
      params:
        timeout: 300               # optional default wait, seconds
        max_question_length: 8000  # optional
```

Arguments: `question`, `question_type` (`CONFIRM`, `FREE_FORM`, `MULTIPLE_CHOICE`, `MULTI_SELECT`, `FORM`), `choices`, `context_data`, and an optional per-call `timeout`. The [system tools reference](../references/system-tools.md#request_human_input) has the full argument and parameter tables.

The companion `inform_user` system tool sends a non-blocking progress update through the same side channel.

### Control flow

1. `RequestHumanInputTool.execute()` (`mas.library.standard.plugins.system_tools.request_human_input`) validates its arguments. It raises `RequestHitlSignal` (`mas.runtime.system_tools.signal`). The signal is control flow, not an error.
2. The local tool provider (`mas.library.standard.plugins.tools.local`) wraps every system tool whose registry entry has `communication: hitl` in `_SystemToolHitlWrapper`. The wrapper catches the signal. It reads `session_id`, `agent_id`, and `correlation_id` from the tool context. It calls `HITLContract.request_approval(...)`.
3. The contract returns `{"choice", "steering", ...}`. The wrapper returns that as the tool result. The agent's turn continues.

The tool call **blocks** while the contract waits. In a delegation chain, the delegating agent waits for the delegate's turn as usual.

### Resolution contracts

`HITLContract` (`mas.runtime.contracts.user_communication_contract`) is the seam. A *contract* is a fixed interface the runtime calls. Implementations:

| Contract | Where | Behaviour |
| --- | --- | --- |
| `RegistryHitlContract` (default, `registry`) | `library-standard` | Registers the request in `HitlResolverRegistry` and waits until `resolve()` is called or the timeout elapses (then raises `TimeoutError`). |
| `AutoResolveHitlContract` (`auto_resolve`) | `library-standard` | Answers at once with `auto_resolve_decision` (default `approve`). Used for unattended runs and benchmarks. |
| `InteractiveHitlContract` | `mas.ctl.session.interactive_hitl_contract` | Prompts on the terminal. `mas-ctl chat -i` uses it, so the question is answered in the same console. |

An agent manifest selects a contract with `spec.hitl_contract`. The value is a plugin name such as `auto_resolve`, or `{type, params}`. `spec.user_io_contract` does the same for `inform_user`. Hosts can also inject one. Benchmarks use `auto_resolve`. Interactive chat uses the terminal contract.

### Side channel: `HitlResolverRegistry`

`HitlResolverRegistry` (`mas.runtime.boundary.hitl.registry`) is a thread-safe table of pending requests. It is process-wide through `get_hitl_resolver_registry()`. Entries are keyed by `(session_id, agent_id, correlation_id)`.

A host that talks to the user (a UI, a chat bridge, a control client) uses the registry to surface and answer questions from any agent, including delegates:

- `subscribe(callback)`: called the moment a request is registered.
- `get_pending_for_session(session_id)`, `get_pending(...)`, and `has_pending(session_id, agent_id=None)`: poll pending requests.
- `resolve(session_id, agent_id, correlation_id, choice=..., steering=...)`: wake the waiting tool call with the user's answer.
- `clear_session(session_id)`.
- `subscribe_user_update` and `get_pending_user_updates_for_session`: the `inform_user` channel.

The delegation contract is unchanged. A delegate still returns text. In multi-agent runs, `mas_session.send()` also records agents that ended a turn with a pending registry request in `state["pending_hitl_agents"]`.

## Not implemented

- **Non-blocking pause and resume.** The original proposal paused the agent's turn (via an `EmitHitlRequest` egress), returned a partial result to the caller, and resumed the turn later through `submit_hitl()`. The shipped tool blocks in the wrapper instead. `request_human_input` has no turn suspension.
- **A `DelegationResult` with HITL state.** Delegation still returns `str`;
  pending questions are only visible through the registry.
- **Cross-process registry.** `HitlResolverRegistry` is in-process. A delegate in another process, for example behind A2A, resolves its questions with its own host's contract.

## See also

- [System tools reference](../references/system-tools.md#request_human_input)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
