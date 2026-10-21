<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 13 — Control attach and debug scripts

> **Packages:** `mas-runtime`, `mas-ctl`, `mas-library-standard`
> **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md),
> [Tutorial 11](../11-sessions-and-recovery/README.md) (checkpoint, fork,
> `--load-checkpoint`) and a configured model. Offline, you can only run
> `mas-ctl validate`. Run all commands from the repository root, in three
> terminals.

## Goal

A long-running agent says something wrong because a tool gave it bad data,
and the live process has moved on. You pause it from another terminal,
persist its exact state, and load that state into a fresh process to fix it.

The bug: `web_search` claims Lyon is the capital of France. You persist the
session, resume a new chat from the file (it still says Lyon), inspect it
from outside, then steer it to Paris. `mas-ctl control resume` only unpauses
the live process. It is not checkpoint resume.

| Step | Terminal | What you do |
| --- | --- | --- |
| 1 | A | Run a session that receives a bad tool result |
| 2 | B | Pause, persist, resume a new chat from the file (says Lyon) |
| 3 | B, C | Inspect and steer the restarted session (says Paris) |
| 4 | A | Auto checkpoints and a checkpoint tree |

## Concepts

- Each A2A (agent-to-agent protocol) connection without a `contextId` is a
  new session. The first response returns the session id as `contextId`. Two
  clients with the same id share one queue.
- `mas-ctl control` attaches to `ControlContract` with that id. `mas-ctl chat`
  advertises it in a runtime directory (`MAS_CONTROL_DIR`,
  `$XDG_RUNTIME_DIR/mas-ctl`, `/var/run/mas-ctl`, then temp). Pass
  `mas-ctl control -d DIR` only if you started chat with `--control-dir DIR`.
- Snapshot and persist need a paused session. `--auto-stop` pauses first.
  A chained CLI line, `-e`, `-f` and `--data @file` use the same command
  language. Agents call `run_control_script` with the same text.
- gdb-style breakpoints are separate. They load from `debug_script` in
  `spec.governance` (the policy layer that can allow, block or change a tool
  call) or from `--debug-script`:

```bash
mas-ctl chat docs/tutorials/13-control-and-debug/agent.yaml
mas-ctl chat docs/tutorials/13-control-and-debug/agent.yaml \
  --debug-script @docs/tutorials/13-control-and-debug/debug.gdb
mas-ctl chat docs/tutorials/13-control-and-debug/agent.yaml \
  --debug-script $'break tool_call calc\ncommands\n  checkpoint\n  continue\nend'
```

In YAML, use `script_file: ./debug.gdb`, `script: @./debug.gdb`, or an inline
`script: |` block. Validate the manifests:

```bash
mas-ctl validate docs/tutorials/13-control-and-debug/agent.yaml
mas-ctl validate docs/tutorials/13-control-and-debug/agent-auto.yaml
```

## Step 1 — Run a session that goes bad

In terminal A:

```bash
mas-ctl chat docs/tutorials/13-control-and-debug/agent.yaml \
  --checkpoint-dir .mas/t13-checkpoints
```

Copy the `session_id: <id>` it prints and keep the terminal open. Type:

```text
You: Remember we will talk about France later. What is 2+2?
You: Look up the capital of France.
```

The `calc` call and the first `web_search` result hit breakpoints in
`debug.gdb`. Each takes an in-memory checkpoint, prints the checkpoint list,
session id and working memory, then continues. The search stub is wrong on
purpose: `Lyon is the capital of France.`

## Step 2 — Interrupt, persist and resume

In terminal B (these forms are equivalent):

```bash
SESSION=<id-from-step-1>

# chained argv
mas-ctl control "$SESSION" pause --reason bad-session persist --label bad --auto-stop inspect checkpoints

# curl-style file
mas-ctl control "$SESSION" --data @docs/tutorials/13-control-and-debug/recover.ctl

# curl-style inline + eval
mas-ctl control "$SESSION" -e 'pause --reason bad-session' -e 'persist --label bad --auto-stop'

# one verb at a time
mas-ctl control pause "$SESSION" --reason bad-session
mas-ctl control persist "$SESSION" --label bad --auto-stop
mas-ctl control inspect "$SESSION"
mas-ctl control checkpoints "$SESSION"
```

Copy `path` from the `persist` output. Fork it, because the live id is still
advertised, then start a new chat from the fork:

```bash
BEFORE=.mas/t13-checkpoints/<file-from-persist>
mas-ctl checkpoint fork "$BEFORE" --session-id t13-bad
FORK=<path-printed-by-fork>
mas-ctl chat --load-checkpoint "$FORK" --checkpoint-dir .mas/t13-checkpoints
```

```text
You: Which country were we going to discuss, and what is its capital?
```

The wording varies; check two facts. You should see **France** (working memory survived) and **Lyon** (you
resumed the bad checkpoint). Paris means you did not load the file. No France
means persist missed working memory. `mas-ctl checkpoint list
.mas/t13-checkpoints` and `mas-ctl checkpoint show "$BEFORE"` dump the files.

## Step 3 — Investigate, then steer the restarted session

In the new chat, ask `What did the search tool tell you about the capital?`.
In terminal C, attach with the new session id printed at chat start:

```bash
RESUMED=<id-from-step-2-chat>
mas-ctl control inspect "$RESUMED"
mas-ctl control checkpoints "$RESUMED"
```

Steer it. You can also fork `$BEFORE` again and steer there.

```bash
mas-ctl control "$RESUMED" --data @docs/tutorials/13-control-and-debug/steer-fix.ctl
# equivalent:
mas-ctl control steer "$RESUMED" \
  --text "Ignore the tool result. The capital of France is Paris, not Lyon. Keep using earlier conversation."
```

Ask the question again in the restarted chat: `Which country were we going to
discuss, and what is its capital?` Model wording varies, so check the country
and capital, not the sentence.

| Session | Country | Capital |
| --- | --- | --- |
| Step 2 chat, after steer | France | Paris |
| Fresh fork of `$BEFORE`, no steer | France | Lyon |

The proof: load the file, still wrong, steer, right.

## Step 4 — Auto checkpoints and a tool-error tree

```bash
mas-ctl chat docs/tutorials/13-control-and-debug/agent-auto.yaml \
  --checkpoint-dir .mas/t13-auto
```

```text
You: What is 2+2?
You: What is 3+3?
```

`calc` fails twice (`fail_first: 2`), then works. `spec.checkpoint.mode:
every_turn` writes a file per completed turn. `backtrack_on_error` restores
an earlier checkpoint after the repeated error and records an
`after-backtrack` node. In-memory checkpoints form a tree (failed path and
retry), not a line.

```bash
mas-ctl checkpoint list .mas/t13-auto
AUTO_SESSION=<session_id-printed-by-the-step-4-chat>
mas-ctl control checkpoints "$AUTO_SESSION"   # another terminal
```

`checkpoint list` shows the files on disk. `control checkpoints` shows the
live in-memory tree. The JSON includes `parent_snapshot_id`, which encodes the tree.

## Observability

Every control verb is logged as `ControlEvent` and emitted on the native
stream as `kind: control`, `category: control.<method>`. Export may filter:

```yaml
spec:
  observability:
    - native:
        categories:
          include: [control, tool, execution]
          exclude: [control.inspect]
```

Without `categories`, everything is written, so pause, persist and steer
appear in `traces/events.jsonl`.

## The gdb script

`debug.gdb` has two breakpoints, `break tool_result web_search if first` and
`break tool_call calc`. Both run:

```text
commands
  checkpoint
  info checkpoints
  info session
  info working_memory
  continue
end
```

`checkpoint` pauses, snapshots and `continue` unpauses. In-memory nodes stay
in the live process. `persist` is what another chat resumes.
`mas-ctl serve` does not take `--checkpoint-dir`.

## What you learned

1. `mas-ctl control <session-id>` pauses, inspects, persists and steers a
   live session from another terminal.
2. A persisted file resumed in a new chat shows the bad state again.
   Steering fixes it reproducibly.
3. `debug.gdb` breakpoints checkpoint automatically, and every control verb
   is logged as a `control` event.

## Reference material

- [Snapshots vs checkpoints](../../references/snapshots.md)
- [Contracts](../../references/contracts.md): `ControlContract`.
- [Kernel operations](../../references/kernel-primitives.md)
- [Session checkpoints](../../manifests/checkpoint.md)
- [Debug-script governance card](https://github.com/outshift-open/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/debug-script.md):
  full `break` / `commands` grammar.

## Next

[Tutorial 14 — Governance and HITL](../14-governance-hitl/README.md) puts a
human (human in the loop, HITL) or an automated policy in front of a tool
call.
