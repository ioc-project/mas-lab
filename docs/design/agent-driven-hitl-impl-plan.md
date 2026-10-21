Status: Historical (implementation plan, superseded; the shipped design is in [agent-driven-hitl.md](agent-driven-hitl.md)).

# Implementation Plan: Agent-Driven HITL Side Channel

This note records the plan for agent-initiated HITL (human in the loop). It is kept as a historical record. The current behaviour is in [agent-driven-hitl.md](agent-driven-hitl.md) and the [system tools reference](../references/system-tools.md#request_human_input).

## Problem it addressed

Only the session that hosted the agent resolved governance-triggered HITL. In a delegation, `mas_session.send()` returned the delegate's text and dropped its HITL state. A question raised by a delegate never reached the user-facing entry point:

```
Moderator (entry)  --delegate("finance", ...)-->  Finance agent
                                                   asks the user a question
                   <--------- text only ----------
Moderator never learns that the question is pending.
```

## Planned approach

1. A `request_human_input` system tool that raises a control-flow signal
   (`RequestHitlSignal`) instead of returning.
2. A wrapper around system tools that catches the signal and hands the
   question to a side channel keyed by session, agent, and correlation id.
3. A thread-safe `HitlResolverRegistry` that any host can poll or subscribe to,
   and resolve.
4. Delegation records which delegates have a pending question. The delegation return type stays the same.
5. Host-side helpers (poll pending questions, resolve one) for user-facing
   clients.

## How it landed

| Planned | Shipped |
| --- | --- |
| Tool returns a `__hitl_request__` marker; the runtime pauses the turn and resumes on resolution | The wrapper (`_SystemToolHitlWrapper` in `mas.library.standard.plugins.tools.local`) **blocks** on an `HITLContract` and returns the answer as the tool result. No marker, no turn suspension. |
| System tools auto-injected into every agent | Opt-in: list `{kind: system, name: request_human_input}` in `spec.tools`. |
| Tool lives in the runtime | Tool lives in `library-standard` (`plugins/system_tools/request_human_input.py`); the runtime keeps only the signal (`mas.runtime.system_tools.signal`) and the registry (`mas.runtime.boundary.hitl.registry`). |
| Single resolution path through the registry | Pluggable `HITLContract`: `RegistryHitlContract` (default), `AutoResolveHitlContract` (unattended), `InteractiveHitlContract` (`mas-ctl chat -i`). |
| Delegation tracks pending delegates | `mas_session.send()` adds them to `state["pending_hitl_agents"]`. |
| Host helper module | Hosts call `HitlResolverRegistry` directly (`subscribe`, `get_pending_for_session`, `resolve`). |

## See also

- [Agent-driven HITL design](agent-driven-hitl.md)
- [Implementation summary](IMPLEMENTATION-SUMMARY.md)
