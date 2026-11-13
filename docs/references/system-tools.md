<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# System tools reference

This page lists every tool a system plugin advertises. A *system plugin* is a runtime syscall the model may call, listed in `spec.system_plugins`. For the rules that enable each plugin, see [System plugins](../manifests/system-plugins.md).

| Tool | Package | Enablement | Declaration aliases |
| --- | --- | --- | --- |
| [`activate_skill`](#activate_skill) | `library-skills` | implicit (`spec.skills`) or declared | `skill-access`, `skill_access` |
| [`list_skill_files`](#list_skill_files) | `library-skills` | with `activate_skill` | — |
| [`read_skill_file`](#read_skill_file) | `library-skills` | with `activate_skill` | — |
| [`run_skill_script`](#run_skill_script) | `library-skills` | implicit (skill `scripts/`, `auto_inject`) or declared | `run-skill-script` |
| [`request_human_input`](#request_human_input) | `library-standard` | declared only | — |
| [`inform_user`](#inform_user) | `library-standard` | declared only | — |
| [`send`](#send) | `library-standard` | declared only (`default_enabled: false`) | — |
| [`pause_session`](#pause_session) | `library-standard` | declared only (`pause_session`); advertised when the session has a control channel | — |
| [`list_checkpoints`](#pause_session) | `library-standard` | with `pause_session` | — |
| [`inspect_session`](#pause_session) | `library-standard` | with `pause_session` | — |
| [`navigate_checkpoint`](#pause_session) | `library-standard` | with `pause_session` | — |
| [`cancel_inflight`](#pause_session) | `library-standard` | with `pause_session` | — |
| [`run_control_script`](#pause_session) | `library-standard` | with `pause_session` | — |
| [`create_subagent`](#create_subagent) | `library-standard` | declared only (`subagents`) | — |

The skill tools require `library-skills`. Without it, declaring one fails with an unknown system plugin error.

Example: grant a system plugin on an agent.

```yaml
spec:
  system_plugins:
    - request_human_input: {timeout: 300}
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

Registered in the standard library with `default_enabled: false`. Naming
`send` in `spec.system_plugins` does not put a tool named `send` in the
model's list; `list_tools` still returns only `delegate_to_<id>`. A user
tool named `send` stays that tool. A user tool named `delegate_to_<id>`
stays that tool unless that peer is on the send allow list.

See [Communication stack](comm-stack.md). Card:
[send.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/system_tools/send.md).

Implementation: `mas.library.standard.plugins.system_tools.send.SendTool`.

## Session-control tools

### `pause_session`

Name `pause_session` in `spec.system_plugins`. The model sees this bundle only
when the session has a control channel: `pause_session`, `list_checkpoints`,
`inspect_session`, `navigate_checkpoint`, `cancel_inflight`, `run_control_script`.

| Tool | Arguments | Notes |
| --- | --- | --- |
| `pause_session` | `reason` (string, required) | Pause at the next turn boundary |
| `list_checkpoints` | none | Snapshot-tree nodes for this session |
| `inspect_session` | none | Live vs debug-cursor snapshot ids |
| `navigate_checkpoint` | `to`, `reason` (strings, required) | Moves the debug cursor; does not change the live run until promote |
| `cancel_inflight` | none | Cancels the in-flight LLM call; tools are not rolled back |
| `run_control_script` | `script` (string, required), `auto_stop` (boolean) | Inline text or `@path`, same as `mas-ctl control --data` |

See [Tutorial 13](../tutorials/13-control-and-debug/README.md).

Implementation: `mas.library.standard.plugins.system_tools.control.PauseSessionPlugin`.

## Orchestration tools

### `create_subagent`

Creates a bounded subagent from a **declared template**, runs it for one
turn, and returns the child's text. Name `subagents` (or the `create_subagent`
shortcut) on the parent manifest.

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

## See also

- [System plugins (manifest rules)](../manifests/system-plugins.md)
- [Spawned subagents](../manifests/subagents.md)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
- [Tutorial 6: agent skills](../tutorials/06-agent-skills/README.md)
