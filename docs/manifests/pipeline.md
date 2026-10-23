<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline manifests

**Package:** `mas-lab-bench`.

A **pipeline** is an ordered list of **pipeline steps** that read **run** artifacts and write CSV and PNG files under `results/`.
A pipeline step is one processor, for example `extract_trace_stats` or `plotnine`.
A run is one execution of the MAS for a scenario, a dataset item, and a repeat index. It writes `events.jsonl`.
Write a pipeline when you need to turn raw run output into tables, plots, or scores.

Two file formats exist. Do not mix them in one file.
Experiments attach steps through **level hooks**: the keys `run.post`, `item.post`, `scenario.post`, and the experiment-root `post:` in `experiment.yaml`.
Run a standalone pipeline file (either format) with `mas-lab benchmark pipeline run PIPELINE_YAML`.

Smallest pipeline file (`pipeline:` root):

```yaml
pipeline:
  name: t3-analysis
  steps:
    - name: extract
      type: extract_trajectories
    - name: plot
      type: plot_trajectory
      depends_on: [extract]
```

Related commands:

- `mas-lab benchmark pipeline plan`: show the execution plan.
- `mas-lab benchmark pipeline show`: show steps and dependencies.
- `mas-lab benchmark pipeline validate`: check steps, dependencies, and types.
- `mas-lab validate pipeline.yaml`: schema check of a `pipeline:` file.

Terms: [glossary.md](../glossary.md). Hands-on: [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).

---

## Post-processing pipeline (`pipeline:` root)

**Schema:** `pipeline.schema.yaml` · **API id:** `pipeline-post`

The benchmark executor runs this format. Use it for analysis graphs embedded in experiments or kept in standalone files.

```yaml
pipeline:
  name: t3-analysis
  output:
    base_dir: ./output
  steps:
    - name: extract
      type: extract_trajectories
      depends_on: []
    - name: plot
      type: plot_trajectory
      depends_on: [extract]
```

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `name` | yes | none | Pipeline id, used in output paths and logs. |
| `description` | no | none | Free text. |
| `output.base_dir` | no | `./output` | Output root, relative to this file. `-o` on the CLI overrides it. |
| `max_active` | no | `8` | Global cap on in-flight scheduler nodes. |
| `steps` | yes | none | At least one step; see [Steps](#steps-types-and-artifacts). A step `name` defaults to its `type`. |

String values in a step's `config` may use any `{key}` passed with `--var key=value`.
Unknown placeholders stay as written.
Environment variables are not substituted.

Steps reference upstream outputs by step name (`@step`) or by a path relative to their level
folder (see [Output locations](#output-locations)).

### Output locations

Steps write their artifacts in the folder of the level they run at (experiment, scenario,
item or run); a step never receives or builds an absolute path. Each step type has a fixed
default file name (`extract_trace_stats` writes `results/trace_stats.csv`). A step config may
set only a relative `subdir` (nested folders allowed), a relative `output` path
(`results/ci.csv`), or, to avoid a collision between two steps of the same type, a simple
`filename` (no separators). Absolute paths, `..`, `~`, drive letters and symlink escapes are
rejected when the pipeline is loaded (`mas-lab benchmark pipeline validate`, `mas-lab validate`)
and again when the step runs; `--var` values may appear inside a relative path but the
resolved path must stay under the level folder.

The single-brace `{output_dir}/...` prefix is a deprecated alias for one release: it is
rewritten to the relative form with a deprecation warning. `{{run.output_dir}}`-style values
were never supported and are rejected.

### Level-scoped steps (`scope:` / `in:` / `out:`)

A step embedded in an experiment's `run:`, `item:`, or `scenario:` block runs once per folder at that level.
The same holds for the experiment-root `pre:` and `post:`.
Its `scope` comes from the enclosing block.
Set `scope:` explicitly only on a standalone pipeline file, which has no enclosing block.

`in:` and `out:` name the artifact a step reads and writes.
Declare the artifact in that level's own `artifacts:` map.
A step whose `in:` names an artifact from the level below fans in every child instance (`config["artifact_paths"]`).
See [experiment.md](experiment.md) and [multi-scenario-format.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/multi-scenario-format.md).

On a standalone `pipeline:` file, `scope:` is one of `run`, `item`, `scenario`, `experiment`.
`test` and `application` are deprecated aliases of `item` and `experiment`.
`phase:` is `pre` or `post` (default `post`).

Experiment manifests use the level keys `run:`, `item:`, `scenario:`, and the experiment-root `post:`.
`test:` is a deprecated alias of `item:` there.

```yaml
run:
  artifacts:
    trace: { type: trace, path: "{run_dir}/traces/events.jsonl" }
    metrics: metrics
  post:
    - name: eval-quality
      type: eval_mce
      in: trace
      out: metrics

item:
  artifacts:
    df: { type: dataframe, path: "{level_dir}/data.csv" }
  post:
    - name: gather-test       # fans in every run's `df` under this item folder
      type: gather_level
      in: df
      out: df
```

---

## Pipeline library (`kind: Pipeline`)

**Schema:** `pipeline-manifest.schema.json` · **API id:** `pipeline`

Store these files under a library's `pipelines/*.yaml`, for example `library-kg/pipelines/`.
Reference one as `LIBRARY:name`. You may omit the `pipelines/` folder and the `.yaml` suffix (see [writing-manifests.md](writing-manifests.md#2-library-prefix)).
Every step needs both `name` and `type`.

The `ui/` PipelineBuilder edits these files.
The controller validates them at `POST /api/libraries/{library}/pipelines/validate`.

```yaml
apiVersion: mas/v1
kind: Pipeline
metadata:
  name: analysis
spec:
  steps:
    - name: extract
      type: extract_trajectories
      depends_on: []
    - name: stats
      type: extract_trace_stats
      depends_on: [extract]
      config:
        subdir: results
```

The UI may write `x-canvas-positions` (canvas layout). Execution ignores it.

---

## Steps, types, and artifacts

An **artifact** is a typed value that one step produces and another consumes.
Artifacts pass in-process during execution.
They are written to files or infra sinks when a step sets `outputs` paths.

- **Dependencies:** `depends_on` builds the step graph. The controller and the executor both detect cycles.
  Independent steps run concurrently, up to `max_active`.
  A **fork** is several steps sharing one artifact or stream (record-level for incremental consumers).
  A **join** is a step that waits on two or more different artifacts until they are sealed.
- **Step types:** registered processors such as `extract_trajectories`, `extract_trace_stats`, `eval_mce`, and `plot_*`.
  See the [pipeline steps catalog](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/pipeline-steps.md).
- **Cache:** each step has a content-addressed **node key** built from its type, version, config, and upstream digests.
  The cache lives under `<output_dir>/.cache/` (a fingerprint sidecar plus an `artefacts/` store).
  An unchanged step restores its persisted outputs and skips execution.
  Force a rerun with `mas-lab benchmark pipeline run ... --force STEP` or `mas-lab benchmark step restart`.
  Parallelism is on by default. Use `--no-parallel` to serialize.
- **Streams:** during a MAS run, native records go on the telemetry channel (in-process, durable log, or `mas-stream/1` WebSocket).
  A step that implements `on_batch` folds that stream in batches while it is open, and calls `finish` when the stream seals.
  A whole-step writer calls `execute` once its inputs are sealed.
  Run-scope fold nodes overlap later MAS runs.
  Higher scopes wait on gates, then the same scheduler runs them. Expansion is plan-based, not a disk scan.
  Demo UIs consume the channel through an ingest subscriber (`kind: metric`, `control.structure`, native observability).
  Metric ticks are ingest-only. They are not republished onto the native run stream.
  `events.jsonl` is the log-sink export of that stream. Consumers do not tail it.
- **Step keys** (standalone `pipeline:` files):
    - `role`: `native`, `wrap`, or `batch` (default `batch`).
    - `resource`: `cpu`, `io`, `net`, or `judge` (default `cpu`).
    - `concurrency: N`: per-type semaphore (default 1).
    - `refresh: progressive`: UI-only partial re-evaluation, never persisted.
    - `batch: {max_records, max_latency_ms}`: how `on_batch` folds are grouped.
    - Output-artifact `shape`: `value`, `records`, or `multi`.
    - `mode`: `append`, `upsert`, or `snapshot`.
    - `records`: named record types for `multi`.
    - `invariants`: checked when the artifact seals.
    - `persist`: `true` for the classic file path, or `always`, `on-seal`, `never` for the artefact store.

  Inline steps in an experiment accept a smaller set. See [experiment.md](experiment.md#artifacts).
- **Defaults:** `max_active: 8`, parallel on, `streams.transport: inproc`.
  Set `bench_emulation.pipeline.max_active` on the experiment, or `pipeline.max_active` on a standalone YAML.
  `engine` and `shadow` on old YAML are ignored.
- **Other `pipeline:` step keys:** `inputs` / `outputs` (in-memory bindings between steps), `phase`, `scope`, and the legacy `per_run` / `per_scenario` expansion flags (superseded by `scope`).

---

## CLI shortcuts

Standalone CLI shortcuts (`mas-lab telemetry push`, `mas-lab kg dump`, ...) map onto these same library pipelines.
Optional library packages register some of them. The `kg` group comes from `library-kg` and appears only when that package is installed.
See [CLI commands and their pipelines](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli-reference.md).

## See also

- [experiment.md](experiment.md)
- [lab.md](lab.md)
- [summarization.md](summarization.md): `eval_mce` judge model
- [Pipeline guide](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/pipeline.md)
