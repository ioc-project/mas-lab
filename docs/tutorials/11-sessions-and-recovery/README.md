<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 11 — Sessions and recovery

> **Packages:** `mas-runtime`, `mas-ctl`
> **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) and
> [Tutorial 1](../01-building-an-agent/README.md), with a model configured.
> Run all commands from the repository root.

## Goal

A session is one running conversation with an agent. A checkpoint is a saved
copy of that session.

You save a conversation, fork it, resume the fork without its original
manifest path, and return to an earlier checkpoint with a steering note.

## Step 1 — Capture a checkpoint

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  --checkpoint-dir .mas/checkpoints --save-checkpoint
```

Complete one turn (it calls your model), then exit with `/quit`. List and inspect the result:

```bash
mas-ctl checkpoint list .mas/checkpoints
mas-ctl checkpoint show .mas/checkpoints/<file-name-from-list>
```

`list` prints one file name per checkpoint, such as `turn-0001.checkpoint.json`.
`show` prints the checkpoint JSON. A version 2 checkpoint holds
the kernel state, the conversation, the manifest content and the session
lineage. Resuming checks the manifest hash, so a changed manifest is
detected.

## Step 2 — Fork and resume

A fork is a new session with its own session id that starts from a
checkpoint.

```bash
mas-ctl checkpoint fork .mas/checkpoints/<file-name-from-list> \
  --output-dir .mas/forks --session-id experiment-a
mas-ctl chat --load-checkpoint <fork-checkpoint-path-printed-by-fork>
```

`fork` prints the new file path, under `.mas/forks/`. Chat loads it with the
manifest stored inside, so you pass no `agent.yaml`.

`mas-ctl checkpoint resume <path>` equals the second command. Each fork has
its own working-memory registry, so sibling forks do not see each other's
changes.

## Step 3 — Know the checkpoint stores

All stores implement `CheckpointStore` (`plugins/checkpoint.py` in
`mas-library-standard`).

| Store | Backs | Survives exit |
| --- | --- | --- |
| `JsonCheckpointStore` | `--checkpoint-dir`, `--save-checkpoint`, `checkpoint fork` | Yes, one `*.checkpoint.json` per checkpoint |
| `InMemoryCheckpointStore` | `/backtrack`, pause/inspect snapshots | No |
| `HybridCheckpointStore` | `spec.checkpoint.storage: hybrid` | Yes, plus an in-process copy for backtrack |

`--save-checkpoint` writes version 2, which the control scripts of
[Tutorial 13](../13-control-and-debug/README.md) read directly.
`retain(session_id, mode, n)` prunes only that session's own files, so
forking never ages out a sibling. Modes: `single`, `last_n`, `all`.

## Step 4 — Recover manually

Add this to the Agent manifest to checkpoint once per completed turn and
keep the last five:

```yaml
spec:
  checkpoint:
    mode: every_turn
    retention:
      mode: last_n
      n: 5
```

In chat, return to the newest retained checkpoint with a steering note:

```text
/backtrack 1 Explain what failed; do not repeat the same tool call.
```

The agent continues from the earlier state and sees your note.

The packaged `openclaw` overlay checkpoints after every model response and
keeps all of them. Automatic recovery from repeated errors is configured
under `spec.governance` (see [Session checkpoints](../../manifests/checkpoint.md)).

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o pkg://mas.library.standard/overlays/openclaw.yaml
```

`/steer` injects operator text into the session. A paused session refuses
the next user turn until you resume it. A checkpoint can also be a cell in an
experiment's starting-state axis: see the
[checkpoint-axis example](../../schemas/examples/checkpoint-axis.yaml).

## What you learned

1. `--save-checkpoint` writes the whole session. `checkpoint fork` and
   `--load-checkpoint` continue it under a new session id.
2. Stores differ in persistence: disk, memory or both.
3. `/backtrack N <note>` returns to an earlier checkpoint with a note.

## Next

[Tutorial 12 — Spawned subagents](../12-subagents/README.md), then
[Tutorial 13 — Control attach and debug](../13-control-and-debug/README.md).
See also [Kernel operations](../../references/kernel-primitives.md).
