<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# System tools reference

This page lists every system tool implemented in MAS-Lab. A *system tool* is a tool the runtime provides to the model, as opposed to a tool you declare in a tool server. For the rules that enable each tool, see [System tools](../manifests/system-tools.md).

| Tool | Package | Enablement | Declaration aliases |
| --- | --- | --- | --- |
| [`activate_skill`](#activate_skill) | `library-skills` | implicit (`spec.skills`) or declared | `skill-access`, `skill_access` |
| [`list_skill_files`](#list_skill_files) | `library-skills` | with `activate_skill` | — |
| [`read_skill_file`](#read_skill_file) | `library-skills` | with `activate_skill` | — |
| [`run_skill_script`](#run_skill_script) | `library-skills` | implicit (skill `scripts/`, `auto_inject`) or declared | `run-skill-script` |
| [`request_human_input`](#request_human_input) | `library-standard` | declared only | — |
| [`inform_user`](#inform_user) | `library-standard` | declared only | — |
| [`send`](#send) | `library-standard` | declared only (`default_enabled: false`) | — |
| [`create_subagent`](#create_subagent) | `library-standard` | declared only | `spawn_subagent` |
| [`open_room`](#open_room) | `library-standard` | declared only | — |

The skill tools require `library-skills`. Without it, declaring one fails with an unknown system tool error.

Example: declare a system tool on an agent.

```yaml
spec:
  tools:
    - kind: system
      name: request_human_input
      params:
        timeout: 300
```

## Skill tools

### `activate_skill`

Loads the full `SKILL.md` body of a catalog skill. The returned body has no frontmatter. Bundled resources are listed.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | Constrained to registered skill names. Names already activated are dropped from the enum. |

Implementation: `mas.library.skills.plugins.sk_tools.SkillToolsPlugin`.

### `list_skill_files`

Lists the files in a skill directory, such as references, scripts, and assets.

| Argument | Type | Required |
| --- | --- | --- |
| `skill` | string | yes |

### `read_skill_file`

Reads one file from a skill directory.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `skill` | string | yes | |
| `path` | string | yes | Relative to the skill directory, such as `references/rules.md` |

### `run_skill_script`

Runs a script from the skill's `scripts/` directory with resource limits.
Returns `stdout`, `stderr`, and the exit code.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `skill` | string | yes | |
| `script` | string | yes | Plain filename, no path |
| `args` | string[] | no | Positional arguments |
| `timeout` | integer | no | Seconds; default 30, maximum 120 |
| `env` | object | no | Extra variables. Loader-control variables such as `PATH` and `LD_PRELOAD` are rejected. |
| `stdin` | string | no | Text sent to the script's standard input |

Implementation: `mas.library.skills.plugins.sk_shell.RunSkillScriptPlugin`.

## User-communication tools

### `request_human_input`

Asks the user a question and **blocks the agent's turn** until the HITL (human in the loop) side channel resolves it. The channel is an `HITLContract`. If none is provided, the HITL resolver registry is used.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `question` | string | yes | Up to `max_question_length` characters |
| `question_type` | string | no | `CONFIRM` (default), `FREE_FORM`, `MULTIPLE_CHOICE`, `MULTI_SELECT`, `FORM` |
| `choices` | string[] | no | Options for `MULTIPLE_CHOICE` and `MULTI_SELECT` questions |
| `context_data` | object | no | Data displayed with the question |
| `timeout` | number | no | Overrides the manifest default for one call |

| Manifest `params` | Default | Notes |
| --- | --- | --- |
| `timeout` | none (wait indefinitely) | Default timeout for each call, in seconds |
| `auto_resolve_decision` | `approve` | Answer used when the host runs unattended (`mas-ctl run-mas` with auto-HITL, benchmarks). |
| `max_question_length` | 20000 | Also sets the schema advertised to the model |

Implementation: `mas.library.standard.plugins.system_tools.request_human_input.RequestHumanInputTool`.

### `inform_user`

Sends a **non-blocking** progress update. The call returns at once and the agent continues.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `message` | string | yes | Up to `max_message_length` characters |
| `user_name` | string | no | |
| `involved_agents` | string[] | no | |
| `metadata` | object | no | |

| Manifest `params` | Default | Notes |
| --- | --- | --- |
| `max_message_length` | 20000 | Also sets the schema advertised to the model |

A `UserIOContract` delivers the update. If none is provided, the HITL resolver registry does.

Implementation: `mas.library.standard.plugins.system_tools.inform_user.InformUserTool`.

### `send`

Registered in the standard library with `default_enabled: false`. Declaring
`{kind: system, name: send}` does not put a tool named `send` in the
model's list; `list_tools` still returns only `delegate_to_<id>`. A user
tool with that name stays that tool.

See [Communication stack](comm-stack.md). Card:
[send.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/system_tools/send.md).

Implementation: `mas.library.standard.plugins.system_tools.send.SendTool`.

## Orchestration tools

### `create_subagent`

Creates a bounded subagent from a **declared template**, runs it for one
turn, and returns the child's text. Alias advertised to the model:
`spawn_subagent`. Declare either name on the parent manifest.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `template` | string | yes | Enum of `params.templates[].id` |
| `task` | string | yes | Task for the child; not a new manifest |

| Manifest `params` | Default | Notes |
| --- | --- | --- |
| `templates` | (required) | `{id, ref, description}` rows; `ref` is a local Agent manifest |
| `max_spawns` | 8 | Session-wide ledger |
| `max_depth` | 3 | Nested create/spawn depth |

The child cannot invent tools or templates. `mas-ctl` tears the instance down after the turn. See [Spawned subagents](../manifests/subagents.md) and
[Tutorial 12](../tutorials/12-subagents/README.md).

Implementation: `mas.library.standard.plugins.system_tools.spawn_subagent.SpawnSubagentTool`
(advertise) plus `mas.library.standard.plugins.engine_tools.subagent_spawner.SubagentSpawner` (run).

### `open_room`

Opens a shared room on a group channel and returns the room's result. The
members post to the channel, read each other's posts there, and answer again
round by round; the caller reads none of it. A member runs a turn when it has
the posts of the last round and posts the reply to the whole channel, so
nothing is relayed. Every member applies the same stop rule to the same posts,
so they stop together; one member (the closer) posts the result. It needs a
link with rooms: `slim` today. On a link without them the call returns an
error text and the turn goes on.

| Argument | Type | Required | Notes |
| --- | --- | --- | --- |
| `task` | string | yes | What the room is asked to settle |

| Manifest `params` | Default | Notes |
| --- | --- | --- |
| `channel` | (required) | Channel name; created on the call |
| `members` | (required) | Agent ids of the members that post (at least two) |
| `round_cap` | (required) | The room stops after this many rounds |
| `post_timeout_s` | (required) | A round waits this long for missing posts, then the room stops |
| `turn` | (required) | What a member is asked each round; `{task}`, `{round}`, `{board}` (the last round's posts as `[member]` blocks) and `{cap}` are filled in |
| `agree_on` | — | Field of each member's closing JSON object that must be the same non-empty value for the room to agree |
| `accept` | `{}` | Fields of that object that must equal these values (`{stance: accept}`) |
| `closer` | first member | The member that posts the result |
| `agreed`, `none` | `{winner}`, `{why}` | The result text; `{winner}`, `{round}`, `{why}`, `{members}`, `{n}`, `{cap}` are filled in |
| `description` | — | Added to the tool description the model sees |
| `timeout` | 1800 | Seconds the call waits for the result |

The rules travel to the members in the `open` message, so a member needs no room config of its own.
A member's position is the last JSON object of its reply. A member whose turn fails ends the room at once, and
the result names it; a member that stays silent ends it after `post_timeout_s`, and the result names it.

Implementation: `mas.library.standard.plugins.system_tools.open_room.OpenRoomTool`
(advertise) plus `mas.library.standard.plugins.engine_tools.room_open.RoomOpen` (run),
over `open_room` on the SLIM link (`library_ioa.plugins.slim.room` and `room_link`).

## See also

- [System tools (manifest rules)](../manifests/system-tools.md)
- [Spawned subagents](../manifests/subagents.md)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
- [Tutorial 6: agent skills](../tutorials/06-agent-skills/README.md)
