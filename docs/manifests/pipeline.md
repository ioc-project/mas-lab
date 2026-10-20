<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline manifests

**Package:** `mas-lab-bench` · Two formats — do not mix them in one file.

A **pipeline** is an ordered list of **pipeline steps** (e.g. `extract_trace_stats`,
`plotnine`) that read **run** artifacts such as **`events.jsonl`** and write CSV/PNG under
`results/`. Level hooks live in `experiment.yaml` (`run.post`, `item.post`,
`scenario.post`, experiment `post:`). A standalone `pipeline:` file is run with
`mas-lab benchmark pipeline run`.

**Terms:** [glossary.md](../glossary.md) · Hands-on: [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).

---

## Post-processing pipeline (`pipeline:` root)

**Schema:** `pipeline.schema.yaml` · **API id:** `pipeline-post`

Used by the **benchmark executor** for analysis DAGs embedded in experiments or standalone files.

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

A step embedded inside an experiment manifest's `run:`/`item:`/`scenario:`/`application:`
block is materialized once per folder at that level; its `scope` is inferred from the
enclosing block (explicit `scope:` is only needed on a standalone pipeline file with no
enclosing block). `in:`/`out:` name the artifact a step reads/writes — declared in that
level's own `artifacts:` map. A step whose `in:` names an artifact from the level *below*
fans in every child instance (`config["artifact_paths"]`); see [experiment.md](experiment.md)
and [multi-scenario-format.md](../../lab/docs/multi-scenario-format.md).

For a standalone `pipeline:` file, the pipeline schema calls the per-item scope `test`;
that is the internal scope name. Experiment manifests use the public level key `item:`
(`test:` is a deprecated alias there).

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
    - name: gather-test       # fans in every run's `df` under this test folder
      type: gather_level
      in: df
      out: df
```

---

## Pipeline library (`kind: Pipeline`)

**Schema:** `pipeline-manifest.schema.json` · **API id:** `pipeline`

Stored under `lab/pipelines/*.yaml`; edited in **mas-lab-ui** PipelineBuilder; validated by
`POST .../pipelines/validate`.

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

UI may emit `x-canvas-positions` (stripped before execution).

---

## Steps, types, and artifacts

- **DAG:** `depends_on` with cycle detection (controller + executor).
  Independent steps run concurrently (`max_active`). **Fork** = several steps
  share one artifact/stream (record-level for incremental consumers). **Join**
  = a step that waits on two or more different artifacts until they are sealed.
- **Step types:** registered processors (`extract_trajectories`, `extract_trace_stats`,
  `eval_mce`, `plot_*`, …). Catalog: [pipeline steps](../../lab/docs/pipeline-steps.md).
- **Artifacts:** typed values passed between steps — in-process during execution; serializable
  to files or infra sinks when a step writes `outputs` paths.
- **Cache:** each step is a content-addressed **node key** (type, version,
  config, upstream digests) under `<output_dir>/.cache/` (fingerprint sidecar
  + `artefacts/` store). Unchanged steps restore persisted outputs and skip
  execute. Force with `mas-lab benchmark pipeline run … --force STEP` or
  `mas-lab benchmark step restart`. Parallelism is on by default
  (`--no-parallel` to serialize).
- **Streams:** During a MAS run native records go on the telemetry channel
  (in-process, durable log, or `mas-stream/1` WebSocket). A step that
  implements `on_batch` folds that stream in batches while it is open and
  `finish`es when it seals. Whole-step writers `execute` once their inputs
  are sealed. Run-scope fold nodes overlap later MAS runs; higher scopes
  wait on gates, then the same scheduler runs them (plan-based expansion,
  not a disk scan). Demo UIs consume the channel through an ingest
  subscriber (`kind: metric`, `control.structure`, native observability).
  Metric ticks are ingest-only — they are not republished onto the native
  run stream. `events.jsonl` is the log-sink export of that stream, not a
  file consumers tail.
- **Step keys:** optional `role`, `resource` (`cpu` / `io` / `net` / `judge`),
  `concurrency: N` (per-type semaphore, default 1), `refresh: progressive`
  (UI-only partial re-eval; never persisted), `batch`,
  artifact `shape` / `mode` / `records` / `invariants` / `persist`.
- **Defaults:** `max_active: 8`, parallel on, `streams.transport: inproc`.
  Set `emulation.pipeline.max_active` on the experiment, or `pipeline.max_active`
  on a standalone YAML. `engine` / `shadow` on old YAML are ignored.

---

Standalone CLI shortcuts (`mas-lab telemetry push`, `mas-lab kg dump`, …)
map onto these same library pipelines. Catalog:
[CLI commands and their pipelines](../../lab/docs/cli-pipelines.md).

## See also

- [experiment.md](experiment.md)
- [lab.md](lab.md)
- [summarization.md](summarization.md) — `eval_mce` judge model
- [Pipeline guide](../../lab/docs/pipeline.md)
