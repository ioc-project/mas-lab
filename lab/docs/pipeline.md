<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline guide

This page explains how pipelines work: hooks, caching, parallelism, and dataflow. Read it before you add or debug a pipeline step.

A **pipeline** is a list of **pipeline steps**. They run after **benchmark** execution and write shared artifacts under `results/`.

Smallest working command: `mas-lab benchmark run experiment.yaml --progress`. Hooks in `experiment.yaml` run automatically.

Terms: [glossary.md](../../docs/glossary.md).

## Embedded vs standalone

An **embedded pipeline** is a set of level hooks inside `experiment.yaml`. It runs automatically with `mas-lab benchmark run`. There is no `pipelines:` attribute.

```yaml
experiment:
  application:
    app: trip-planner
    configs_dir: ./overlays
  run:
    artifacts:
      trace: trace
      df: dataframe
    post:
      - name: extract-trace-stats
        type: extract_trace_stats
        in: trace
        out: df
  item:
    artifacts: {df: dataframe}
    post:
      - {name: gather-item, type: gather_level, in: df, out: df, depends_on: [extract-trace-stats]}
  scenario:
    artifacts: {df: dataframe}
    post:
      - {name: gather-scenario, type: gather_level, in: df, out: df, depends_on: [gather-item]}
  post:
    - name: gather-experiment
      type: gather_level
      in: df
      out: df
      depends_on: [gather-scenario]
```

CLI `--depth exp|scenario|item|run` selects which hooks to inspect.

Two keys are deprecated: `application.post` (use experiment-level `post:`) and `test:` (use `item:`).

A **per-run hook** is `run.post`. It runs once after each individual run `(scenario × item × run_idx)`:

```yaml
experiment:
  run:
    n_runs: 3
    post:
      - ref: pipelines/post-run.yaml    # file ref; steps inside use per_run: true
```

Steps in a `run.post` pipeline receive the run's execution context through `ctx.scope_context` (scenario, item, run index). Set `per_run: true` on each step that needs per-run access:

```yaml
# pipelines/post-run.yaml
spec:
  steps:
    - name: eval-booking
      type: lib/steps/my_eval.py:MyEvalStep   # path relative to experiment dir
      per_run: true
      config:
        reference_path: datasets/reference/fares.yaml
```

Hook levels, outermost to innermost:

| YAML key | Fires | Typical use |
|----------|-------|-------------|
| experiment `post:` | Once, after all scenarios complete | CI aggregation, figures |
| `scenario.post` | Once per scenario, after all items | Per-scenario summaries |
| `item.post` | Once per (scenario × item), after all runs | Cross-run comparison |
| `run.post` | Once per run | Per-run evaluation, OTel export |

A **standalone pipeline** is a separate YAML file. Use it when **runs** already exist:

```bash
mas-lab benchmark pipeline run labs/.../pipeline-figure.yaml -o $XDG_DATA_HOME/mas/labs/my-exp
```

Schema: [manifests/pipeline.md](../../docs/manifests/pipeline.md).

## Per-run artifact access

Inside a `per_run: true` step, use these helpers. Never read `.run_ref` directly:

```python
from mas.lab.benchmark.pipeline.run_artifacts import resolve_run_events, run_dir_from_ctx

async def execute(self, ctx):
    run_dir    = run_dir_from_ctx(ctx, self.config)   # benchmark r1/ dir
    events_path = resolve_run_events(ctx, self.config) # handles .run_ref + inline
```

`run_dir_from_ctx` derives the run directory from `ctx.scope_context` when present (the `per_run: true` path). It falls back to `config["run_dir"]` for backward compatibility.

`resolve_run_events` handles all storage modes: inline `traces/events.jsonl`, a symlinked `traces/` directory, and `.run_ref` content-addressed cache indirection.

## Step catalog

[pipeline-steps.md](pipeline-steps.md).

## Step caching

Each step has a **node key**: a hash of its type, step version, config, and upstream output digests.

The key is stored in two places: `<output_dir>/.cache/<step>.fingerprint` and a manifest under `.cache/artefacts/<aa>/<key>/`. In-memory outputs (DataFrames, JSON) are snapshotted next to the sidecar. A cache hit restores them without re-running the step.

A later `mas-lab benchmark run` or `benchmark pipeline run` skips a step when its node key is unchanged. Downstream steps rerun when an upstream key changes.

```bash
mas-lab benchmark step restart <benchmark-id> <step-id>   # one step
mas-lab benchmark pipeline run pipeline.yaml -o DIR --force STEP
```

For the relation to the run-level trace cache, see [benchmark CLI](../src/mas/lab/cli/commands/benchmark/) and [benchmark-state-architecture.md](benchmark-state-architecture.md).

Command reference: [cli-reference.md](cli-reference.md) (`mas-lab benchmark pipeline run|plan|show|validate`).

## Parallelism, fork, and join

The scheduler is the executor. Independent steps run concurrently, capped by `max_active` (default 8). Parallelism is on by default. Pass `--no-parallel` to serialize for debugging. Resource classes (`cpu`, `io`, `net`, `judge`) keep their own limits.

- **Fork**: several steps share one upstream artifact (the same stream). Incremental consumers subscribe to that stream and fold records as they arrive. They run in parallel with each other and with the producer. Non-incremental consumers wait until the producer finishes, then run as siblings.
- **Join** (branch collect): a step that `depends_on` two or more different artifacts starts only after every input is sealed or done. Records are not zipped unless the step does that itself.

`concurrency: N` on a step spec is a per-type semaphore: N instances of that type run at once. It is not a wrapper step. The scheduler expands every instance into its own node.

The standalone CLI is `mas-lab benchmark pipeline run PIPELINE.yaml`. Embedded `benchmark run` uses the same default.

## Artifacts and dataflow

Declare artifacts on the enclosing level (`run.artifacts`, `item.artifacts`, and so on). A step's `in:` and `out:` name those artifacts. `depends_on` is the DAG edge.

At execute time the input is `ctx.step_outputs[dep].data`. Run-scope steps also get `run_input_stream` (`events.jsonl`, run dir).

| Path | What waits |
|------|------------|
| **Fork** | Several steps share one upstream stream. Incremental consumers fold records as they arrive. Whole-step consumers wait until the producer seals. |
| **Join** | `depends_on` two or more different artifacts. The step starts only after every input is sealed or done. |
| **Item gate** | All MAS runs under that item are terminal. It does not wait for item-scope steps. Those still use `depends_on`. |
| **Scenario / experiment gates** | Fire when every child item or scenario gate completes. Experiment `post:` in a live DAG (for example lab-smoke `extract_trace_stats`) therefore starts after every planned run is terminal. |

Run-scope steps that implement `on_batch` subscribe to that run's channel while the stream is open. They finish when it seals. A step can also call `ctx.publish(record)` from `execute`. The executor then seals that stream instead of re-dumping the StepOutput.

## Live telemetry

While a MAS run is in progress, the runtime publishes native records on an in-process telemetry channel. An optional durable log lives under the trace-cache streams directory. A second process can read the WebSocket `mas-stream/1`.

`extract_trace_stats` folds that stream into `kind: metric` snapshots for demo-ui.

All post scopes share one scheduler:

- A run-scope step starts when its own run becomes terminal. Later MAS executions may still be running.
- Item, scenario, and experiment steps wait on gates, then on `depends_on`.
- Incremental fork consumers fold records as they arrive.
- Whole-step writers `execute` once their inputs are sealed.

`events.jsonl` is an export of the native bodies. It is not the source of truth.

## Custom steps

[custom-pipeline-steps.md](custom-pipeline-steps.md) is the authoring guide. It covers the `per_run: true` pattern, artifact resolution APIs, fixture evaluation, `register_steps.py`, and the `pkg://` vs scheme syntax for tool refs.
