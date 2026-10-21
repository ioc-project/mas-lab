<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# System tools

A **system tool** is a tool that the runtime owns and implements, so agent authors do not write it.
Examples: load a skill, run a skill script, ask the user a question, send the user a progress update, and spawn a subagent from a pre-authored template.
Declare one in `spec.tools` when the agent needs a runtime capability.
Alternatively, let `spec.skills` enable the skill tools implicitly.

```yaml
spec:
  tools:
    - kind: system
      name: inform_user
```

System tools reach the model through the same [`ToolContract`](../references/tool-contract.md) as application tools.
They are not loaded from a `kind: Tool` manifest.
The runtime discovers them from the `system_tool` category in the plugin registry.
The registry is filled from the `library.yaml` of each installed library, and each entry carries a `default_enabled` attribute.
Each tool is either **implicit** (another part of the manifest implies it) or **declared** (listed by name).

The [system tools reference](../references/system-tools.md) lists every tool with its arguments and parameters.

## Rule: the manifest lists what the model sees

An agent is offered a system tool only when one of these conditions holds:

1. **Implicit.** Another part of the manifest requires the tool.
2. **Declared.** `spec.tools` contains `{kind: system, name: <tool>}`. A declared entry is on unless it sets `enabled: false`.
3. **Host-requested.** The embedding host passes the tool in `system_tools`. This is a programmatic API; see [Host-requested tools](#host-requested-tools).
4. **Registry default.** The tool's library entry sets `attributes: {default_enabled: true}`. No shipped system tool does.

`enabled: false` on a declared entry wins over every other source, including a host request.
No boolean enables every system tool at once.
An agent with no skills and no declarations gets no system tools.

## Implicit enablement

| Tool(s) | Enabled when |
| --- | --- |
| `activate_skill`, `list_skill_files`, `read_skill_file` | `spec.skills` lists at least one skill |
| `run_skill_script` | a listed skill ships a non-empty `scripts/` directory |
| `run_skill_script` | a `context_sources` plugin sets `auto_inject: true` |

These tools follow from the skills themselves.
A skill without a way to load it leaves the model with a catalog entry it cannot use.
Scripts without `run_skill_script` leave instructions the model cannot follow.

```yaml
spec:
  skills:
    - answer-formatting        # → activate_skill (+ list/read)
    - log-analyzer             # has scripts/ → run_skill_script
```

## Declared enablement

List a system tool in `spec.tools` with `kind: system`.
The same entry carries its `params`:

```yaml
spec:
  tools:
    - ref: ./tools/calculator.tool.yaml
    - kind: system
      name: request_human_input
      params:
        timeout: 300
        auto_resolve_decision: reject
    - kind: system
      name: inform_user
      params:
        max_message_length: 8000
```

Declaration is the only way to enable the user-communication tools, `request_human_input` and `inform_user`.
They are never implicit.
If a model asks the user when nothing in the protocol can answer, it can substitute a made-up or rubber-stamped answer for a real one.
The caller cannot detect the substitution.
Give an agent these tools only when its deployment can route questions and updates to a real user.

You can also declare the skill tools explicitly, for example to offer `run_skill_script` for a skill whose scripts are resolved elsewhere.
An unknown name, such as a typo, fails at load time and lists the available names.
The runtime does not skip it silently.

`create_subagent` (alias `spawn_subagent`) is opt-in and carries its configuration in the same entry.
`params.templates` must declare at least one pre-authored Agent manifest.
Optional `params.max_spawns` and `params.max_depth` bound the spawns.
The model sees both names.
See [Spawned subagents](subagents.md) and [Tutorial 12](../tutorials/12-subagents/README.md).

`pause_session` is also opt-in.
Declaring it gives the model a bounded session-control surface:

- `pause_session`
- `list_checkpoints`
- `inspect_session`
- `navigate_checkpoint`
- `cancel_inflight`
- `run_control_script`

The runtime offers these tools only when the session has a control channel attached.
See [Tutorial 13](../tutorials/13-control-and-debug/README.md).

Third-party system tools register with a library manifest (`library.yaml`, see [Libraries](../libraries.md)):

```yaml
apiVersion: mas/v1
kind: Library
name: example-audit-tools
types: [system_tool]
plugins:
  - type: system_tool
    name: audit_event
    urn: mas.system_tool.audit_event
    module: example.audit_tools
    class: AuditEventTool
    attributes: {default_enabled: false}
```

## Host-requested tools

A program that embeds the runtime can add system tools without editing the manifest:

```python
build_manifest_tool_provider(
    tools_spec,
    manifest_dir,
    system_tools=["request_human_input", "inform_user"],
)
```

The runtime merges host-requested names with the manifest declarations.
The manifest still supplies their `params`.

## Overlays

`spec.tools` merges with `list_ops`, so an overlay can enable a system tool for one environment without changing the base agent:

```yaml
# overlays/interactive.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: interactive
spec:
  target:
    kind: Agent
  patch:
    tools:
      $op:
        add:
          - kind: system
            name: request_human_input
```

The same agent then runs in batch mode without a human in the loop (HITL, a person who answers questions and approves actions), and interactively with one.
Only the overlay changes.
Overlay patches accept the system-tool names `request_human_input`, `inform_user`, `activate_skill`, and `run_skill_script`.
Declare `create_subagent` and `pause_session` in the agent manifest itself.

## See also

- [System tools reference](../references/system-tools.md): every tool, with arguments, parameters, and where it is implemented.
- [Agent manifest](agent.md): `spec.tools` and `spec.skills` fields.
- [Tutorial 1: building an agent](../tutorials/01-building-an-agent/README.md): the implicit `activate_skill` in practice.
- [Tutorial 6: agent skills](../tutorials/06-agent-skills/README.md): skill scripts and `run_skill_script`.
