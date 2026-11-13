<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# System plugins

A **system plugin** is a syscall the runtime owns. The model may call the tools
it advertises. Examples: load a skill, ask the user a question, send a progress
update, create a subagent from a pre-authored template. Declare one in
`spec.system_plugins` when the agent needs that capability. `spec.skills` can
also enable the skill plugins implicitly.

```yaml
spec:
  tools:
    - ref: ./tools/get_metrics.tool.yaml
  system_plugins:
    - inform_user
    - request_human_input: {max_question_length: 400}
```

The plugin, not the tool, is the unit: it owns session-scoped state and may
expose several tools. `subagents` advertises `create_subagent` over one spawn
ledger. A session checkpoint and an in-memory snapshot restore that state.

System plugin tools reach the model next to application tools. They are routed
to the plugin, never to a `ToolProvider`. A tool in an application provider
that uses a system-plugin tool name is refused at load.

The runtime discovers plugins from the `system_plugin` type in the plugin
registry (`library.yaml`).

The [system tools reference](../references/system-tools.md) lists every advertised
tool with its arguments.

## Rule: the manifest lists what the model sees

An agent is offered a system plugin only when one of these conditions holds:

1. **Named.** `spec.system_plugins` lists the plugin. A named entry is on unless
   it sets `enabled: false`.
2. **Implicit.** Another part of the manifest requires it (`spec.skills` for
   `activate_skill`; a listed skill with `scripts/` for `run_skill_script`). The
   plugin's own rule decides.
3. **Default.** The plugin's library row sets `default_enabled: true`. No shipped
   plugin does.
4. **Host-requested.** The embedding host passes the plugin in `system_plugins=`.

`enabled: false` on a named entry turns the plugin off whichever of the other
conditions would load it, host requests included. An unknown name, including
`enabled: false` of an unknown plugin, is a load error. An agent with no skills
and no named plugins gets none.

A plugin is named by its URN, its name, or a shortcut of its row (`create_subagent`
names `subagents`). The match is exact; a fragment of a name is unknown.

## Implicit enablement

| Plugin | Enabled when |
| --- | --- |
| `activate_skill` (tools `activate_skill`, `list_skill_files`, `read_skill_file`) | `spec.skills` lists at least one skill |
| `run_skill_script` | a listed skill ships a non-empty `scripts/` directory |
| `run_skill_script` | a `context_sources` plugin sets `auto_inject: true` |

```yaml
spec:
  skills:
    - answer-formatting        # → activate_skill (+ list/read)
    - log-analyzer             # has scripts/ → run_skill_script
```

## Named enablement

```yaml
spec:
  tools:
    - ref: ./tools/calculator.tool.yaml
  system_plugins:
    - request_human_input:
        timeout: 300
        auto_resolve_decision: reject
    - inform_user:
        max_message_length: 8000
```

`send` does not put a tool named `send` in the model's list; `list_tools` still
returns only `delegate_to_<id>`. A user tool named `send` stays that tool. A
user tool named `delegate_to_<id>` stays that tool unless that peer is on the
send allow list.
See [Communication stack](../references/comm-stack.md).

`request_human_input` and `inform_user` are never implicit. Give an agent these
plugins only when its deployment can route questions and updates to a real user.

`subagents` is opt-in. `create_subagent` is the tool name. Settings include
`templates`, `max_spawns`, and `max_depth`. See [Spawned subagents](subagents.md)
and [Tutorial 12](../tutorials/12-subagents/README.md).

`pause_session` is opt-in. Naming it gives the model a bounded session-control
surface (`pause_session`, `list_checkpoints`, `inspect_session`,
`navigate_checkpoint`, `cancel_inflight`, `run_control_script`) when the session
has a control channel. See [Tutorial 13](../tutorials/13-control-and-debug/README.md).

Third-party system plugins register with a library manifest:

```yaml
apiVersion: mas/v1
kind: Library
name: example-audit
types: [system_plugin]
plugins:
  - type: system_plugin
    name: audit_event
    urn: mas.system_plugin.audit_event
    module: example.audit
    class: AuditEventPlugin
    attributes: {default_enabled: false}
```

## Host-requested plugins

```python
build_manifest_tool_provider(
    tools_spec,
    manifest_dir,
    system_plugins=["request_human_input", "inform_user"],
)
```

## Deprecated spellings

Each still works and warns once, naming its replacement. `mas-ctl validate` warns
per `{kind: system}` entry.

| Deprecated | Use | Behaviour today |
| --- | --- | --- |
| `{kind: system, name: X, params: {...}}` in `spec.tools` | `X: {...}` in `spec.system_plugins` | Grants the same plugin; `params` become its settings; `enabled: false` turns it off. When both name one plugin, `spec.system_plugins` wins. |
| `spawn_subagent` | `subagents` (tool `create_subagent`) | Names the same plugin. |
| `system_tools=` argument of the host loaders | `system_plugins=` | Same list. |
| plugin type `system_tool` | `system_plugin` | A registered row loads through an adapter. Named settings reach the tool constructor. Its `communication` attribute (`hitl` / `user_io`) still wraps the tool. |

## Overlays

`spec.system_plugins` merges by plugin id (`plugin_list_ops`), so an overlay can
grant a plugin for one environment. `mas-ctl validate` on an Overlay errors on
an unknown plugin name in `$op.add` / `$op.remove`.

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
    system_plugins:
      $op:
        add:
          - request_human_input
```

This page used to be `system-tools.md`.

## See also

- [System tools reference](../references/system-tools.md): every advertised tool.
- [Agent manifest](agent.md): `spec.system_plugins`, `spec.tools`, `spec.skills`.
- [Tutorial 1: building an agent](../tutorials/01-building-an-agent/README.md).
- [Tutorial 6: agent skills](../tutorials/06-agent-skills/README.md).
