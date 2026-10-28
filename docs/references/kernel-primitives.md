<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Kernel operations

This page lists the small set of operations the kernel exposes. Read it if you write a plugin, drive a session remotely, or debug a run.

Terms used here:

- The *kernel* is the core of the runtime. It holds the agent state and the state machines that advance it.
- A *Mealy machine* is a state machine whose output depends on the current state and the input. One step is a *tick*. See the [Mealy machines guide](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/mealy-machines-guide.md).
- A *plugin* is a unit of behaviour that a manifest selects by name. A *session* is one live run you drive interactively.
- A *snapshot* is an in-memory copy of run state. See [Snapshots vs checkpoints](snapshots.md).

## The problem

Subagents, recovery, and debugging look like separate features. They are not. Each combines the same few operations. A caller learns one set of verbs.

Example: a what-if run uses three operations in order.

1. `snapshot` saves the state.
2. `branch` forks a run from that snapshot.
3. `promote` makes the branch the live run.

## Operations

| Operation | Meaning |
| --- | --- |
| Start / stop | Materialize a spec into a running agent. Tear it down. |
| Step | Apply current state and input to get the next state. Goes through governance. Written to the trace. |
| Spawn | Start a child agent with a parent id. |
| Snapshot | Take a cheap in-memory picture of the run (kernel, working memory, spec revision). |
| Persist | Write that picture to disk. |
| Navigate | Move a debug cursor on the snapshot tree. The live run does not change until promote. |
| Branch / promote | Fork a what-if run from a snapshot. Promote replaces the live run with the branch. |
| Pause / resume / steer / undo | Control the run. Pause blocks the next user turn. |
| Spec revision | Record a change to the live spec, such as disabling a tool or switching a pattern. |

The canonical names are the frozen set `KERNEL_OPS` in `mas.runtime.harness.catalog`: `materialize`, `teardown`, `step`, `spawn`, `snapshot`, `persist`, `navigate`, `pause`, `resume`, `steer`, `undo`, `spec_delta`, `branch`, `promote`.

Plugin code, an LLM tool advertisement, and an admin caller (`ControlContract`) call the same functions.

## Sessions and A2A

*A2A* (Agent2Agent) is the protocol people and agents use to talk to an agent. A2A `contextId` is the session id.

- Two A2A connections with no `contextId` are two sessions.
- The first response returns the session id as `contextId`.
- A later connection that sends that id attaches to the same session.
- Concurrent attachers share one `TurnInputQueue`.

Multi-host attach is a library `control_protocol` plugin (JSON-lines over unix or TCP sockets).

Inbound user turns are a subset of `ControlContract`:

- A2A `message/send` is `send_message`. It queues a turn at the tail.
- A2A has no steer RPC. `steer` exists only in the control protocol.
- `AgentCommContract.send` is an outbound peer send after delegation. It is not user input. `inform` and `ask` are peer sends, not steer. See [Communication stack](comm-stack.md) for what is a send and what is control.
- `UserIOContract` carries progress output.
- `input-required` is HITL (human in the loop: a person approves an action). It is not a splice.

## Concurrent queue

All queue mutations are serialized by a lock per session.

| Verb | Concurrent rule |
| --- | --- |
| `send_message` | A2A **Send Message** (`message/send`). Always succeeds. Tail only, `action="turn"`. Extra input on a non-terminal working task waits in the queue. Never preempts. |
| `enqueue_input` | Control-protocol queue mutation. Default `at="tail"`. `at="head"` or `at=<index>` (from `inspect_queue`, range `[0, len]`) also work. May set `action="steer"` for a later cycle. Never conflicts. |
| `cancel_queued` / drop | CAS on `revision`. A gone id raises `QueueItemGone`. |
| `reorder_queue` / move | CAS on `revision`. Must list every live id once. |
| `set_queued_action` (queue to steer) | CAS on `revision`. Changes a still-queued item. |
| `steer` (`preempt`) | Keep streamed tokens, stop the rest of this decode, continue. The decode is preempted, not a finished client response. Unavailable on A2A. |
| `steer` (`replace`) | Discard streamed tokens, stop this decode, start a new exclusive turn. Unavailable on A2A. |
| `steer` (`after`) | Do not interrupt. Queue `text` at the front (`at="head"`) and wait for the current generation to finish. `enqueue` is an alias. |
| `cancel_inflight` | A2A **Cancel Task** (`tasks/cancel`). Drops the rest of the decode. No follow-up cycle. Success is not guaranteed. |

CAS means compare-and-swap: the caller passes the `revision` it last saw. `inspect_queue` returns that token. Callers retry on `QueueConflict`. There is no last-write-wins. Turns drain FIFO under the same lock, so a drop cannot race a pop.

## LLM primitives

These control verbs are not Mealy ticks. They close or continue an `LLM_CALL` already in `M_model=CALLING`. `M_model` is the model-I/O machine. `M_dp` is the design-pattern machine.

| Verb | LLM | `M_model` | Reply |
| --- | --- | --- | --- |
| `steer` (preempt) | Cancel the streamed `ainvoke` between tokens. Keep the `append_partial` prefix in working memory. Start a new completion after `OperatorSteerReceived` (operator text in context). | CALLING to IDLE (abort, not DONE), then CALLING | The prefix is not a client `stop`. The continuation is a new assistant message. |
| `steer` (`replace`) | Cancel `ainvoke`, discard partials. Start a new `UserInputReceived` turn. | CALLING to IDLE, `M_dp` IDLE | No prefix in history. Fresh user turn. |
| `steer` (`after`) | Leave the in-flight call alone. | unchanged | The current decode finishes normally (`stop`), then the queued turn runs. |
| `cancel_inflight` | Cancel `ainvoke`, drop the remainder. | CALLING to ERROR/IDLE via cancelled return | Error/cancel client response. No follow-up. |

Preempt needs a cancellable decode. `ainvoke` uses `achat_completion_stream` when the provider has it. Cancel then lands between chunks, even if the spec did not set `stream: true` for the UI. Without a stream iterator, cancel aborts the one-shot await and the prefix is empty.

`finish_reason=preempted` or `replaced` on `EngineIoReturn` means that **decode** ended. It does not mean the agent finished answering. Evaluate does not emit `EmitClientResponse` for those.

## Control directory and scripts

`mas-ctl chat` and `mas-ctl serve` advertise each session id (A2A `contextId`) in a control directory. The first writable candidate wins:

1. `$XDG_RUNTIME_DIR/mas-ctl`
2. `/run/user/<uid>/mas-ctl`
3. `/var/run/mas-ctl`
4. the process temp dir

`MAS_CONTROL_DIR` overrides the list. `mas-ctl control attach SESSION_ID` uses the default. Pass `--no-control` to skip advertising.

`mas-ctl control` scripts (`pause`, `persist --auto-stop`, `steer`) use one language. It works chained on the CLI, passed as `-e`, `-f FILE`, or `--data @file`. Snapshot and persist require a paused session unless you pass `--auto-stop`.

## Debug scripts

Gdb-like debug scripts are a `runtime`-type plugin (`debug_script`, shortcut `gdb`, in `mas-library-standard`). They observe tool calls and tool results so the process acts as a debugger. `evaluate_egress` always returns ALLOW.

Enable the plugin from the workspace `config.yaml` `plugins:` or `lab.enable_plugins`. It is off unless listed. `spec.debug` holds `script_file` or an inline `script`. You can also load one with `mas-ctl chat --debug-script @file`.

Commands:

- `break tool_call` and `break tool_result`. `if first` is valid only on `tool_result`.
- `checkpoint`, `info checkpoints`, `info working_memory`, `continue`.

Breakpoint hits emit native telemetry (`category: debug.breakpoint`). `checkpoint`, `pause`, and `resume` go through `ControlContract`.

## Checkpoint storage

Checkpoint storage is a library `checkpoint_store` plugin (`memory`, `disk`, `hybrid`) behind `persist`. See [Session checkpoints](../manifests/checkpoint.md).

Workspace `config.yaml` `plugins:` and `lab.enable_plugins` turn on gdb and checkpoint stores. Both are off unless listed. `spec.observability` and `spec.governance` stay on the agent.

## Layers

1. **Kernel operations**: the table above.
2. **Boundary slots**: closed set of envelope and spec types. `BOUNDARY_SLOTS` in `mas.runtime.harness.catalog` lists them: `design_pattern`, `governance`, `context_manager`, `summarizer`, `assembler`, `observability`, `memory`, `tool_provider`, `llm_provider`, `agent_comm`, `system_tool`, `hitl_contract`, `user_io_contract`, `engine_tool_provider`. A new slot is a kernel change.
3. **Harness compositions**: named combinations whose leaves are operations or slots (`react`, `subagents`, `recovery`, `detective`, `whatif`, `plan_mode`, `evolution`). See `mas.runtime.harness`.
4. **Library and product**: skills, related filesystem adapters, execute sandboxes, lab steps. These are not envelope slots. Kernel product state `Q` does not store them.

After an ALLOW decision, tool execution may run inside an `ExecuteSandbox` (workdir or host jail). Coding-agent workspaces are snapshotted as related state (fingerprint and locator; `git_worktree` or `copy_dir` adapters). See [Snapshots vs checkpoints](snapshots.md).

## Copy-on-write

Python updates kernel state in place. A snapshot copies kernel product state into a frozen `CowKernel` and freeze-shares working memory. Later writes do not leak into the frozen node. Restore installs a writable copy.

## See also

- [Snapshots vs checkpoints](snapshots.md)
- [Contracts](contracts.md)
- [Thin waist](thin-waist.md)
- [Tutorial 13: control and debug](../tutorials/13-control-and-debug/README.md)
- [Tutorial 11: sessions and recovery](../tutorials/11-sessions-and-recovery/README.md)
