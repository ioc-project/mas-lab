<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline guide

A **pipeline** is a list of **pipeline steps** that run after **benchmark**
execution and write shared artifacts under `results/`.

Terms: [glossary.md](../../docs/glossary.md).

## Embedded vs standalone

**Embedded pipeline** — level hooks inside `experiment.yaml`; runs automatically
when you `mas-lab benchmark run`. There is no `pipelines:` attribute.

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

CLI `--depth exp|scenario|item|run` selects which of these hooks to inspect.
Deprecated: `application.post` (use experiment-level `post:`) and `test:` (use `item:`).

**Per-run hook** — `run.post`; runs once after **each individual run**
`(scenario × item × run_idx)`:

```yaml
experiment:
  run:
    n_runs: 3
    post:
      - ref: pipelines/post-run.yaml    # file ref; steps inside use per_run: true
```

Steps in a `run.post` pipeline receive the run's execution context via
`ctx.scope_context` (scenario, item, run index).  Use `per_run: true` on each
step that needs per-run access:

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

**Hook levels** (outermost → innermost):

| YAML key | Fires | Typical use |
|----------|-------|-------------|
| experiment `post:` | Once, after all scenarios complete | CI aggregation, figures |
| `scenario.post` | Once per scenario, after all items | Per-scenario summaries |
| `item.post` | Once per (scenario × item), after all runs | Cross-run comparison |
| `run.post` | Once per run | Per-run evaluation, OTel export |

**Standalone pipeline** — separate YAML; use when **runs** already exist:

```bash
mas-lab benchmark pipeline run labs/.../pipeline-figure.yaml -o $XDG_DATA_HOME/mas/labs/my-exp
```

Schema: [manifests/pipeline.md](../../docs/manifests/pipeline.md).

## Per-run artifact access

Inside a `per_run: true` step, use the canonical helpers — **never** read
`.run_ref` directly:

```python
from mas.lab.benchmark.pipeline.run_artifacts import resolve_run_events, run_dir_from_ctx

async def execute(self, ctx):
    run_dir    = run_dir_from_ctx(ctx, self.config)   # benchmark r1/ dir
    events_path = resolve_run_events(ctx, self.config) # handles .run_ref + inline
```

`run_dir_from_ctx` derives the run directory from `ctx.scope_context` when
present (the `per_run: true` code path), with a `config["run_dir"]` fallback
for backward compatibility.  `resolve_run_events` handles all storage modes:
inline `traces/events.jsonl`, symlinked `traces/` directory, and `.run_ref`
content-addressed cache indirection.

## Step catalog

[pipeline-steps.md](pipeline-steps.md).

## Step caching

Each step is addressed by a **node key** (hash of type, step version, config,
and upstream output digests). The key is stored as
`<output_dir>/.cache/<step>.fingerprint` and as a manifest under
`.cache/artefacts/<aa>/<key>/`. In-memory outputs (DataFrames, JSON) are
snapshotted next to the sidecar so a cache hit restores them without
re-running the step.

A later `mas-lab benchmark run` (or `benchmark pipeline run`) skips a step
when that node key is unchanged. Downstream steps rerun when an upstream
key changes.

```bash
mas-lab benchmark step restart <benchmark-id> <step-id>   # one step
mas-lab benchmark pipeline run pipeline.yaml -o DIR --force STEP
```

See [benchmark CLI](../src/mas/lab/cli/commands/benchmark/) and
[benchmark-state-architecture.md](benchmark-state-architecture.md) for
how this relates to the run-level trace cache.

CLI shortcuts onto these pipelines: [cli-pipelines.md](cli-pipelines.md).
Man pages: [docs/cli/mas-lab.md](../../docs/cli/mas-lab.md).

## Parallelism, fork, and join

The scheduler is the executor. Independent steps run concurrently, capped by
`max_active` (default 8). Parallelism is **on by default**; pass
`--no-parallel` to serialize for debugging. Resource classes
(`cpu`, `io`, `net`, `judge`) still have their own limits.

**Fork** — several steps share one upstream artifact (same stream). Incremental
consumers subscribe to that stream and fold **records** as they arrive, in
parallel with each other and with the producer. Non-incremental consumers wait
until the producer finishes, then run as sibling steps.

**Join** (branch collect) — a step that `depends_on` two or more different
artifacts starts only after every input is sealed / done. Records are not
zipped unless the step itself does that.

`concurrency: N` on a step spec is a per-type semaphore (N instances of
that type at once), not a wrapper step. The scheduler expands every
instance to its own node.

Standalone CLI: `mas-lab benchmark pipeline run PIPELINE.yaml` (parallel on;
`--no-parallel` to serialize). Embedded `benchmark run` uses the same default.

## Artifacts and dataflow

Declare artifacts on the enclosing level (`run.artifacts`, `item.artifacts`,
…). A step's `in:` / `out:` names those artifacts; `depends_on` is the DAG
edge. Input at execute time is `ctx.step_outputs[dep].data` plus, for
run-scope steps, `run_input_stream` (`events.jsonl`, run dir).

| Path | What waits |
|------|------------|
| **Fork** | Several steps share one upstream stream. Incremental consumers fold records as they arrive. Whole-step consumers wait until the producer seals. |
| **Join** | `depends_on` two or more *different* artifacts. The step starts only after every input is sealed / done. |
| **Item gate** | All MAS runs under that item are terminal. Does **not** wait for item-scope *steps* — those still use `depends_on`. |
| **Scenario / experiment gates** | Fired when every child item / scenario gate completes. Experiment post in a live DAG (for example lab-smoke `extract_trace_stats`) therefore starts after every planned run is terminal. |

Run-scope steps that implement `on_batch` subscribe to that run's channel
while the stream is open and finish when it seals. A step may also
`ctx.publish(record)` from `execute`; the executor seals that stream instead
of re-dumping the StepOutput.

## Live telemetry

While a MAS run is in progress, native records are published on an
in-process telemetry channel (optional durable log under the trace-cache
streams directory; WebSocket `mas-stream/1` for a second process).
`extract_trace_stats` folds that stream into `kind: metric` snapshots for
demo-ui. **All** post scopes share one scheduler: run-scope starts when
*that* run becomes terminal (overlapping later MAS executions); item /
scenario / experiment wait on gates, then on `depends_on`. Incremental
fork consumers fold records as they arrive; whole-step writers still
`execute` once their inputs are sealed. `events.jsonl` is an export of the
native bodies, not the source of truth.

## Custom steps

[custom-pipeline-steps.md](custom-pipeline-steps.md) — authoring guide,
`per_run: true` pattern, artifact resolution APIs, fixture evaluation,
`register_steps.py`, and the `pkg://` vs scheme syntax for tool refs.
