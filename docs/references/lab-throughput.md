<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab run throughput

How `mas-lab benchmark run` spends wall time as `schedule.parallel_scenarios` grows.

A **run** is one execution of the agent or MAS for a given scenario, item, and repeat index. `schedule.parallel_scenarios` is how many runs may be in flight at once. Default `schedule.worker_processes` is 1: each in-flight run is a thread in the batch process. `N>1` runs each of those in a spawn child.

## Persistent append handles

`JsonlFileEmitter` and the lab stream log (`LogChannel`) each keep one handle per file. A line is on disk before `emit` / `publish_sync` returns (the emitter writes each whole line unbuffered in append mode, so two emitters or threads on one path never interleave a line; the stream log handle is line-buffered and flushed), so a reader tailing the file during a run still sees it. `JsonlFileEmitter` truncates the file once, when it is created, as it always did. The emitter handle closes from the run's observability shutdown; an `emit` after `close` raises. A stream handle closes in `seal_sync`, and a stream that never seals closes at batch end; a `publish` after that `close` raises.

Before this, each native event opened `traces/events.jsonl` for append, wrote one line, and closed it, and the stream log did the same. Threads overlap that wait, so scaling flattens while the process is waiting on `open`. After the handles stay open, process workers (`schedule.worker_processes`) help CPU-heavy runs more than wait-heavy ones.

## Per-batch caches

A lab batch builds one `BatchCache` and puts it on `RunContext.batch_cache`. Parsed manifests, infra files and control documents are keyed by path and checked by content hash (a same-size edit within the timestamp resolution is still re-read, and an edit replaces the entry of its path). Each run gets a copy, so it cannot change what the next run reads. Library `entry_points` and installed roots are memoized until `clear_library_discovery_cache` at batch start and end. Schema registries skip a second directory stamp while the batch cache holds them. Nothing that carries a session id, a credential, a tag or a bound flavour is stored.

## Cheaper per event

A stream frame is serialized once (`StreamFrame.encoded`) and that line is what the log writes. `LiveRunSink` takes the frame from `publish_frame_sync` and does not copy the whole stream list. The rolling digest still runs. `follow_worker` polls with `stdout_from` / `stderr_from` and the daemon returns only the new bytes. A batch that names a trace cache rebinds the channel to that root even if a channel was already installed. `reset_channel` restores the previous log, including for threads that never set one.

## MCP loop threads

Each `MCPClientWrapper` starts its own `mcp-loop` thread.

## Process workers

`schedule.worker_processes` (default `1`) is how many child processes run the MAS. `1` is today's threads in the batch process. `N>1` starts `N` workers with the `spawn` start method (not `fork`: the daemon already has threads and sockets). Each worker keeps one `BatchCache` for the batch and rebuilds each run context from a plain job. A job is `(scenario, item, run)` plus paths and YAML trees; nothing pickles a `RunContext` or a plugin, and a job value that is not plain JSON (a non-string key, a set, bytes, a datetime, a plugin object) is refused with an error naming where it sits. The parent publishes `run.started` before the job and seals the stream after it; every event a worker emitted for a job reaches the parent before that job's result is returned. If those events are not delivered in time, the job is one error row (`worker events not delivered`). A second job with the same `job_id` while the first is in flight is one error row (`duplicate job_id`).

Services stay in the parent on the ports the experiment already named. Workers only connect. The parent writes `results.csv` in plan order, not completion order. A worker that dies marks its in-flight run as an error (retryable per `max_attempts`) and is replaced; the rest of the batch continues.

How the worker count is checked: the schedule parse only checks that `worker_processes` is an integer of at least 1, so an experiment loads on any machine. A batch that starts with more workers than the machine has cores is refused with a message naming both numbers.

Lifetime of a worker:

- `schedule.worker_max_runs` (omit to keep a worker for the batch): the parent retires a worker when it has taken that many jobs, before it goes back to the free queue, so the next job never reaches a worker that is about to leave.
- `schedule.timeout` starts when a worker is leased. Waiting for a free worker does not count against it.
- A worker polls its parent (once a second while idle) and exits when the parent is gone, so a parent killed without cleanup does not leave workers behind.
- The pool is created right before the first run is dispatched and is closed on every path out of the batch.

Pipeline steps run in the parent. A step that patches the parent's process state (a replaced function, an environment change) would not reach the workers, so with `N>1` every pipeline step type must declare `worker_safe`: `worker_safe = True` on the step class, or `worker_safe: true` under `attributes` of its library entry. Shipped library steps declare it there. A step that does not declare it, or declares `False`, is refused at batch start; there is no default.

A script that starts a batch from Python (for example `asyncio.run(run_mas_benchmark(...))`) must be guarded by `if __name__ == "__main__":`. Workers start with `spawn`, which imports the main module again in each child; an unguarded script would start the batch again inside the worker, and the worker dies.

What differs from thread mode, and why it does not change results:

- Emulation plugins are rebuilt for each job (thread mode builds one set per batch). The default MAS runner does not consume `emulation_plugins`, so no result depends on them. A custom runner that keeps state in them gets a fresh set per job.
- A worker does not push the demo UI scope or subscribe `forward_native_to_channel`; the parent publishes the worker's events through the live sink instead. During a run the demo UI ingest endpoint is posted only `run.terminal` in both modes.

Process workers help CPU-heavy runs once open-handle wait is gone. They do not help a lab that is still waiting on per-event `open`.

## Free-threaded Python (3.14t)

Importing the runtime, the lab CLI, and the default A2A plugin does not import `grpc`, so a 3.14t interpreter can stay free-threaded. Extra `a2a-grpc` of `mas-lab-library-ioa` still needs `grpcio`, which has no free-threaded wheel. Which packages install on 3.14t: [Python versions](../python-support.md).

## Schedule knobs that affect wall time only

`schedule.parallel_scenarios`, `schedule.worker_processes`, `pause_between_runs`, `timeout`, `max_attempts` and `retry_backoff_s` never change what a run means. See [Experiment: schedule](../manifests/experiment.md#schedule).

## See also

- [Experiment manifest](../manifests/experiment.md)
- [Event model](event-model.md) (`JsonlFileEmitter`, stream log)
- [Python versions](../python-support.md)
