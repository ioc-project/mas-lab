<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Spawned subagents

A **subagent** is a temporary child agent that a parent agent creates during one turn.
`create_subagent` (alias `spawn_subagent`) is a **system tool** (a tool the runtime provides, declared with `kind: system`; see [system-tools.md](system-tools.md)).
The parent LLM calls it to start one pre-authored Agent manifest for a single task.
The child's final response becomes the tool result.

Declare it when an agent should hand a bounded task to a temporary specialist.
It differs from `delegate_to_<id>`: delegation calls a peer in the MAS topology, while a subagent is a temporary child built from a named template.

Add this declaration under an Agent manifest:

```yaml
spec:
  tools:
    - kind: system
      name: create_subagent
      params:
        templates:
          - id: reviewer
            ref: ./agents/reviewer.yaml
            description: Review the proposed change
        max_spawns: 8
        max_depth: 3
```

## Enablement

Declaring the system tool is the whole contract.
There is no separate capability flag.
An agent without this entry cannot spawn.

The runtime advertises both `create_subagent` and `spawn_subagent` to the model, and either name works.
Each call takes two arguments:

- `template`: one of the declared template IDs.
- `task`: non-empty text for the child.

| Param | Required | Default | Meaning |
|-------|----------|---------|---------|
| `templates[].id` | yes | none | Template ID the model passes as `template`; unique within the manifest. |
| `templates[].ref` | yes | none | Path to an Agent manifest, relative to this manifest. It must stay under the allowed roots. The file is validated as `kind: Agent` when the agent starts. |
| `templates[].description` | no | `""` | Shown to the model next to the template ID. |
| `max_spawns` | no | `8` | Child runs allowed per session. |
| `max_depth` | no | `3` | Maximum nesting depth. A child that itself declares the tool can spawn. |

At least one template is required.
An empty list fails when the agent starts.
The model selects a declared template and supplies its task.
It cannot create a manifest or grant new tools at runtime.
Set `enabled: false` on the entry to turn the tool off.

## Bounds and lifecycle

The default session budget allows eight child runs at a nesting depth of three.
`SpawnLedger`, the per-session counter in `mas-ctl`, enforces both limits.
A call over budget returns the tool result `blocked: depth or spawn-count budget exceeded` instead of failing the turn.

Each call follows the same steps:

1. Build one child from the template.
2. Register it on the local bus.
3. Run one turn and return the result.
4. Remove the instance and endpoint, including on error.

Children do not persist between calls.
The child keeps the parent call's identity for trace attribution.

A child runs synchronously on the default engine path.
On the async path (`ainvoke`), several spawn calls in one parallel tool-call batch run concurrently on the same event loop.
Bounds still apply.
See [MAS concurrency model](mas.md#concurrency-model).

### Not used yet

An Overlay `tools` patch cannot add `create_subagent` in this release.
The overlay schema accepts only `request_human_input`, `inform_user`, `activate_skill`, and `run_skill_script` as system-tool names.
Declare `create_subagent` in the agent manifest.

## See also

- [Tutorial 12](../tutorials/12-subagents/README.md): an incident coordinator that creates researcher and reviewer specialists
- Runnable manifest pair: [coordinator](../schemas/examples/subagent-agent.yaml) and [reviewer template](../schemas/examples/subagent-reviewer.yaml)
- [system-tools.md](system-tools.md): every system tool
- [agent.md](agent.md#tool): the `kind: system` tool form
