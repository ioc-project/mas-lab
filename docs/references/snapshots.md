<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Snapshots vs checkpoints

This page explains how a run saves its state, restores it, and branches from it. Read it when you pause, fork, or resume a session.

## The problem

You want to rewind an agent run, try another path, or continue it in a new process. That needs a copy of the run state at one point in time.

- A *snapshot* is an in-memory copy of that state.
- A *checkpoint* is a snapshot written to disk, so it survives a process restart.

A *session* is one live run that you drive interactively. The *kernel* is the small core of the runtime that holds the agent state machine. See [Kernel primitives](kernel-primitives.md).

## Example: pause, save, resume

```bash
mas-ctl control persist --auto-stop      # write the checkpoint file
mas-ctl checkpoint fork ...              # prepare a copy to resume from
mas-ctl chat --load-checkpoint PATH      # continue in a new process
```

## Definitions

- A **snapshot** is an in-memory node. It holds kernel state, working memory, the spec revision, and optional related-state fingerprints (the execute workspace). Taking one does not write to disk.
- A **checkpoint** is a snapshot written to disk. `persist(snapshot, store)` is the only bridge.

## Save and resume

1. Pause the session.
2. Run `mas-ctl control persist`. Add `--auto-stop` to pause and persist in one step.
3. In another process, run `mas-ctl checkpoint fork`.
4. Run `mas-ctl chat --load-checkpoint`.

`mas-ctl control resume` only unpauses the live session. It does not load a checkpoint.

## The snapshot tree

Snapshots form a tree. Walking the tree moves a debug cursor. That does not change the live run. Promoting a branch replaces the live run. Discarding `session.branch()` restores the origin.

Restore also restores the spec revision current at that node. The same tools are enabled as when the node was taken.

## Related workspace state

File bytes are not part of kernel state. A `RelatedStatePlugin` (plugin type `related_state`) stores a fingerprint and a locator on the snapshot. `library-standard` ships two adapters:

- **git_worktree**: runs `commit-tree` onto `refs/mas-lab/...` with a private index. It does not move `HEAD` or the user index.
- **copy_dir**: copies a directory by content hash. Use it in tests and on hosts without git.

Restore replays those adapters. `SnapshotTree.from_events` rebuilds the tree identity from trace events. Bodies stay on the snapshot or the adapter.

## When snapshots are taken

- On an explicit `take_snapshot` or `branch`.
- At every governance decision at the egress (authorize) and ingress (validate) chokepoints, when governance is enabled for the run. *Governance* is the policy layer that approves or blocks tool calls.

## Why working memory is frozen

Governance snapshots run during a transition that still mutates the live object. So the kernel `QProduct` is copied into a frozen `CowKernel`. Working memory freezes in place.

The conversation archive is the same CoW grain: append-only `chunks{}` are
shared; each snapshot stores a **covering head** (id list + `archive_view_id`).
Live `/backtrack` retargets that pointer instead of restoring a second copy of
every message.

Disk `persist()` has two depths. **Shallow** (`portability: reference`) writes
the head and relies on `traces/archive/` (every chunk, not overflow-only).
**Deep** (`portability: self_contained`) hydrates payloads so the checkpoint
is portable without the archive. Archive disabled ⇒ deep only. See
[Deep vs shallow](../manifests/checkpoint.md#deep-vs-shallow) and
[Live backtrack](context-management.md#live-backtrack-copy-on-write-heads).

## See also

- [Kernel primitives](kernel-primitives.md)
- [Thin waist](thin-waist.md)
- [Tutorial 11: sessions and recovery](../tutorials/11-sessions-and-recovery/README.md)
- [Tutorial 13: control and debug](../tutorials/13-control-and-debug/README.md)