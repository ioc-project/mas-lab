<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Session checkpoints

A **session checkpoint** saves the runtime kernel state and the conversation history as one resumable file.
Enable checkpoints with `spec.checkpoint` on an Agent manifest when a session must survive a crash, be forked, or be rewound.
Version 2 files also hold the session lineage and the agent manifest, so a new process can rebuild the session without the original manifest file.
A checkpoint must not contain inline secrets.
Keep credentials in environment or secret-store references.

```yaml
spec:
  checkpoint:
    mode: every_turn
```

Schemas:

- Policy: `spec.checkpoint` in [`agent.schema.yaml`](../schemas/runtime/agent.schema.yaml).
- File format: [`checkpoint.schema.yaml`](../schemas/checkpoint.schema.yaml). Version 1 holds a kernel snapshot. Version 2 adds `lineage`, `working_memory`, and the embedded `manifest`.

## Contents

- [Policy](#policy)
- [Resume and fork](#resume-and-fork)
- [Backtracking](#backtracking)
- [See also](#see-also)

## Policy

`spec.checkpoint` defaults to no automatic checkpointing.
Set the cadence (`mode`) and the location (`storage`) on an Agent manifest.
Alternatively, apply the bundled `openclaw` overlay (`pkg://mas.library.standard/overlays/openclaw.yaml`).
That overlay turns on `on_event` checkpoints after every LLM call and `auto_resume_latest`.
It also adds the `backtrack_on_error` recovery binding shown under [Backtracking](#backtracking).

A full policy looks like this:

```yaml
spec:
  checkpoint:
    mode: every_turn
    storage:
      kind: hybrid
      path: .mas/checkpoints
    triggers: [after_llm_call]
    retention:
      mode: last_n
      n: 5
    portability: self_contained
    auto_resume_latest: false
```

| Setting | Values | Default |
| --- | --- | --- |
| `mode` | `none`, `in_memory`, `on_event`, `every_turn` | `none` |
| `storage` | `memory`, `disk`, `hybrid`, or `{kind, path}` | inferred from `mode` |
| `triggers` | `after_llm_call`, `after_tool_call`, `before_destructive_tool`, `every_n_turns` | `[]` |
| `every_n_turns` | Positive integer; used with the `every_n_turns` trigger | unset |
| `retention.mode` | `all`, `single`, `last_n` | `all` |
| `retention.n` | Positive integer for `last_n` | `5` |
| `portability` | `self_contained`/`deep`, `reference`/`shallow` | `self_contained` |
| `auto_resume_latest` | Boolean | `false` |

`mode` says **when** to capture:

- `none` captures nothing automatically. Manual checkpoints still work (`--save-checkpoint`, control `persist`).
- `on_event` captures on the listed `triggers`.
- `every_turn` captures after each completed user turn.
- `in_memory` keeps checkpoints in the process.

When `storage` is omitted, `mode: in_memory` means memory; `on_event` and
`every_turn` mean disk. `auto_resume_latest` requires disk or hybrid.

## Deep vs shallow

A well-built archive makes `/backtrack` a covering-head retarget: the
checkpoint does not copy every message. Two portability modes:

| Mode | Aliases | What is stored | When |
| --- | --- | --- | --- |
| `self_contained` | `deep` | Hydrated payloads (resolve every pointer, like copying through symlinks) | A single snapshot, or when the archive is off |
| `reference` | `shallow` | Covering ids + `archive_view_id` + `persist_dir` | Reverse debugging, every-checkpoint traces, `/backtrack` |

The archive must be **fully** on disk for shallow to work — not only
folded overflow. It colocates with the run's events file:

```text
<run_root>/
  traces/events.jsonl
  traces/archive/          # every leaf and summary
  checkpoints/             # heads (shallow) or full blobs (deep)
```

`mas_ctl.archive.enabled` defaults to **false**. Then a checkpoint **must**
be deep: there is nothing to point at. The runtime falls back from
`reference` to `self_contained` in that case.

Defaults: archive off; checkpoint `mode: none` and
`portability: self_contained`. One explicit `--save-checkpoint` is a
deep blob and does not need the archive. Reverse debugging: set
`mas_ctl.archive.enabled: true` and `portability: shallow`.

Harness overlays (`pkg://mas.library.skills/overlays/harness-profiles/`)
pick the variant — cadence, portability, skill pin, overflow — so each
framework's behaviour is a spec overlay, not a fork of the runtime.

A workspace or lab may enable a store via `config.yaml` `plugins:` /
`lab.enable_plugins` (`mas.checkpoint_store.hybrid`, …) when the spec omits
`storage`. Spec `storage` still wins. CLI `--checkpoint-dir` overrides
`storage.path`. When disk persistence is
enabled without either, the CLI uses `.mas/checkpoints` beside the active
manifest (or current working directory). Retention prunes only files belonging
to the same session.
`auto_resume_latest: true` loads the newest version 2 checkpoint in the checkpoint directory whose embedded manifest hash matches the current manifest.
It requires `disk` or `hybrid` storage.

`storage` says **where** payloads live.
The runtime resolves it as a library `checkpoint_store` plugin that implements kernel `persist`.
It is not a new envelope slot.

- `memory`: process-local. `/backtrack` works, but a new process cannot load the checkpoint.
- `disk`: JSON files. Use `checkpoint list`, `checkpoint fork`, and `--load-checkpoint`.
- `hybrid`: memory plus files. The in-process copy is fast, and the disk copy is what a resume loads.

When `storage` is omitted, `mode: in_memory` means memory, and `on_event` and `every_turn` mean disk.
A workspace or lab can enable a store through `config.yaml` `plugins:` or `lab.enable_plugins` (for example `mas.checkpoint_store.hybrid`) when the spec omits `storage`.
Spec `storage` still wins.

The checkpoint directory resolves in this order:

1. CLI `--checkpoint-dir` overrides `storage.path`.
2. Otherwise `storage.path` applies.
3. When disk persistence is on and neither is set, the CLI uses `.mas/checkpoints` beside the active manifest, or in the current working directory.

Retention prunes only files that belong to the same session.

[Tutorial 11](../tutorials/11-sessions-and-recovery/README.md) walks through crash, resume, fork, and backtrack.

## Resume and fork

Create an explicit checkpoint with `--save-checkpoint`, or enable automatic checkpoints through the policy:

```bash
mas-ctl chat agent.yaml --checkpoint-dir .mas/checkpoints --save-checkpoint
```

List the artifacts and fork one into a new session identity:

```bash
mas-ctl checkpoint list .mas/checkpoints            # prints file names
mas-ctl checkpoint show .mas/checkpoints/<file>     # pretty-prints the JSON
mas-ctl checkpoint fork .mas/checkpoints/<file> \
  --output-dir .mas/forks                           # optional: --session-id ID
mas-ctl chat --load-checkpoint <fork-checkpoint-path-printed-by-fork>
# or: mas-ctl checkpoint resume <checkpoint-path>
```

- `list` prints file names only. Prefix them with the directory.
- `fork` requires a version 2 checkpoint and checks the embedded manifest hash. It prints its output path, by default next to the source file.
- `resume` is `chat --load-checkpoint` with no manifest argument. For version 2, `--load-checkpoint` uses the embedded manifest when you pass no manifest argument.

Resume preserves the session ID and the history.
Fork creates a new session ID, records its parent and source checkpoint, and copies the history up to that point.
Later turns do not share mutable memory.
Version 1 checkpoints stay loadable as kernel-only snapshots when you pass an explicit manifest.

A **snapshot** is a copy of kernel state taken inside a live process.
An in-memory snapshot tree is cheaper than a checkpoint.
`take_snapshot` does not write disk.
Walking the tree moves a debug cursor and does not change the live run until you promote a branch.
See [Snapshots vs checkpoints](../references/snapshots.md).
Pause (`ControlContract.pause`) blocks the next user turn until resume.

## Backtracking

Backtracking rewinds a session to an earlier checkpoint and steers the model away from a failure.
In a managed chat with checkpoints enabled, rewind manually:

```text
/backtrack 1 Avoid the failed tool approach and ask for missing details first.
```

Automatic recovery is opt-in through an error-recovery governance binding:

```yaml
spec:
  checkpoint:
    mode: every_turn
  governance:
    - sample:
        error_recovery_plugin: backtrack_on_error
        backtrack:
          repeat_threshold: 2
          max_backtracks_per_session: 3
          steps: 1
          on_cap_reached: hitl
```

The recovery plugin first retries the same engine error.
Once its signature repeats, the kernel emits a backtrack boundary signal.
The session layer then restores the retained checkpoint and injects a steering message.
Design-pattern plugins do not own this policy.

`on_cap_reached` accepts `hitl`, `terminate`, or `stop_backtracking`.
`hitl` (human in the loop) hands the decision to a person.

Live `/backtrack` in the same process retargets the archive **covering
head** (copy-on-write of the id list) instead of deepcopying every message.
Disk resume still serializes. See
[Live backtrack](../references/context-management.md#live-backtrack-copy-on-write-heads).

## See also

- [Tutorial 11: sessions and recovery](../tutorials/11-sessions-and-recovery/README.md)
- [Agent manifest](agent.md): `spec.checkpoint`
- [Governance](governance.md): the `error_recovery_plugin` binding
- [Snapshots vs checkpoints](../references/snapshots.md)