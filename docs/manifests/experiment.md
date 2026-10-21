<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Experiment manifest (`experiment:`)

**Package:** `mas-lab-bench` · **Schema:** `experiment.schema.yaml`

An **experiment** manifest tells `mas-lab benchmark run` what to execute.
It compares variants of one application on a fixed set of inputs. It names:

- the **application**: the MAS or agent under test;
- the **scenarios**: named setups, each a stack of **overlays** (small YAML patches merged onto the application manifest);
- the **dataset**: the list of inputs, where each entry is an **item**;
- the **runs**: one **run** is one execution for a scenario, an item, and a repeat index; `n_runs` sets the repeats;
- the **pipeline steps** (processors that turn run output into tables and plots) that build `results/` afterwards.

The file has a single top-level key, `experiment:`.
Only `name` and an application binding (`application:`) are required.
The loader resolves relative paths from the directory that contains the experiment YAML.

Smallest useful experiment (two scenarios, one dataset, three runs per item):

```yaml
experiment:
  name: pattern-compare
  models:
    main: gpt-4o-mini
  application:
    manifest: ./agent.yaml
    configs_dir: ./overlays
  scenarios:
    - id: cot
      overlays: {logic: [cot], control: [], infra: []}
    - id: react
      overlays: {logic: [react], control: [], infra: []}
  dataset:
    path: ./dataset.yaml
  run:
    n_runs: 3
```

Check a file without running it:

```bash
mas-lab validate experiment.yaml          # schema + deprecations + referenced files
mas-lab benchmark run experiment.yaml --dry-run   # also plans the run matrix
```

`mas-lab benchmark run` accepts repeatable `--override` flags.
The CLI patches the `experiment:` root before validation, scenario expansion, and dataset loading:

```bash
mas-lab benchmark run experiment.yaml \
  --override 'experiment:experiment.run.n_runs=1' \
  --override 'experiment:experiment.dataset.limit=2'
```

Put reusable selector and wildcard groups in an Overlay manifest under `spec.overrides`.
The [Overlay manifest reference](overlay.md) defines the target, patch, precedence, and validation rules.

## Contents

- [Fields you always set](#fields-you-always-set): [top-level keys](#top-level-keys), [applications](#applications), [scenarios](#scenarios), [dataset](#dataset), [run levels](#run-levels-and-n_runs), [model slots](#model-slots-models-model)
- [Fields you sometimes set](#fields-you-sometimes-set): [evaluation model](#evaluation-model-evaluationmodel), [level hooks and artifacts](#artifacts), [checkpoint axis](#checkpoint-axis)
- [Advanced fields](#advanced-fields): [design vs schedule](#design-vs-schedule), [bench emulation](#bench_emulation), [replay](#replay), [`output_schema`](#output_schema)
- [Common mistakes](#common-mistakes)

---

## Fields you always set

### Top-level keys

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `name` | yes | none | Experiment id; used in output paths and reports. |
| `application` | yes¹ | none | MAS or agent under test: `app` or `manifest`, plus optional `configs_dir`, `base_scenario`. See [Applications](#applications). |
| `scenarios` | no | none | Overlay columns. See [Scenarios](#scenarios). |
| `dataset` | no | none | Items to run. See [Dataset](#dataset). |
| `run` | no | none | Run level section: `n_runs` (default `3`), `artifacts`, `pre`, `post`. |
| `model` / `models` | no | `any` | Model slots. See [Model slots](#model-slots-models-model). |

¹ Exactly one binding is required: `application` (preferred), or the deprecated `applications` / `mas`.

### Applications

`experiment.application` is the MAS pointer.
It is either an `app:` catalog id or a `manifest:` path (a `mas.yaml`, or an `agent.yaml` for a single-agent app).

```yaml
application:
  app: example-library:my-app@v1   # or: manifest: ./mas.yaml
  configs_dir: ./overlays          # optional
  base_scenario: baseline          # optional (default: baseline)
```

| Field | Meaning |
| --- | --- |
| `app` | Catalog id, optionally library-qualified and versioned (`example-library:my-app@v1`). See [writing-manifests.md](writing-manifests.md#3-version-grammar). |
| `manifest` | Path (or `LIBRARY:path`) to `mas.yaml` / `agent.yaml`, relative to the experiment file. |
| `configs_dir` | Directory in which scenario overlay ids are looked up. Defaults to `overlays/` next to the resolved manifest. |
| `base_scenario` | Scenario id used as the reference baseline. Default `baseline`. |

One of `app` or `manifest` is required.
Prefer `app: library:id@version` for shared apps. Relative `manifest:` paths still work.

### Scenarios

A **scenario** is one column of the result matrix: a named setup built from overlay stacks.

```yaml
scenarios:
  - id: baseline
    overlays: {logic: [], control: [], infra: []}
  - id: react
    description: ReAct pattern
    tags: [pattern]
    overlays:
      logic: [pattern-react]          # overlay id from configs_dir
      control: [{ref: ./overlays/strict-governance.yaml}]
      infra: []
    flavour: local                    # optional per-scenario flavour
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique scenario id; also the run directory name. |
| `overlays` | no | Three overlay stacks applied in order `logic`, `control`, `infra`. Each entry is an overlay id (resolved in `configs_dir`) or `{ref: path}`. When `overlays` is present, all three keys are required; use `[]` for an empty stack. |
| `description` | no | Label shown in reports. |
| `tags` | no | Grouping and filtering labels. |
| `flavour` | no | Per-scenario flavour name or path. A **flavour** is a deployment preset; see [flavour.md](flavour.md). |

When `overlays` is omitted, the scenario `id` is used as the single `logic` overlay.
`overlays` must be a mapping. A bare list is rejected.
See [overlay.md](overlay.md) for the overlay format.
See [Topology, workflow, and routing](mas.md#topology-workflow-and-routing) for topology overlays.

### Dataset

`experiment.dataset` points at the items to run.
`name` is a catalog id (with `locator: samples`) or a lab-local `datasets/<name>.yaml`.
Nested experiments (`01-foo/experiment.yaml`) resolve the lab root `datasets/` folder automatically.
[dataset.md](dataset.md) defines the Dataset format.

```yaml
dataset:
  name: trip-planner-benchmark
  locator: samples
  limit: 5
```

| Field | Meaning |
| --- | --- |
| `name` | Dataset catalog id (`my-dataset`, `my-app-cases@v1`), or the stem of `datasets/<name>.yaml` next to the experiment or lab root. |
| `locator` | Library scheme (for example `samples`) whose `library.yaml` catalogs `name`. |
| `path` | Explicit path to a Dataset YAML. Wins over `name`. |
| `app` | Legacy hint: look for `datasets/<name>` under that app. Prefer a catalog `name`. |
| `filter` | Keep only items whose metadata equals every given value (for example `{category: hitl}`). |
| `limit` | Keep the first N items (smoke tests, CI). |
| `source` | Same shape as the Dataset's `spec.source`; merged over it (for example a different `split` or a tighter `limit`). |

Use `limit` for smoke tests and CI. Do not check in a reduced copy of the same pack (`*-100.yaml`).

### Run levels and `n_runs`

An experiment has four levels. A **level hook** is a `pre:` or `post:` list of pipeline steps attached to one level.
There is no `pipelines:` key. Steps live on the same levels that `mas-lab benchmark show <id> -r` prints.
The flags `--depth`, `--scenario`, `--item`, and `--run` filter that tree.

| YAML | `benchmark show` filter | Scope | Typical post |
| ------ | ----- | ------- | -------------- |
| `pre:` / `post:` (experiment root) | `--depth exp` | Whole experiment | `gather_level`, CI, plots |
| `scenario:` | `--scenario` / `--depth scenario` | One overlay column | Gather item frames |
| `item:` | `--item` / `--depth item` | One dataset item (all runs) | Gather run frames |
| `run:` | `--run` / `--depth run` | Single run index | `eval_mce`, `metrics_to_dataframe`, `extract_trace_stats` |

Each level takes `pre:` and `post:` as lists of steps (0 to N entries).
`run:` also carries `n_runs` (default `3`).

`application:` is the MAS binding (`app` / `manifest` / `configs_dir`), not a level.
Deprecated aliases still load with a warning: `applications:`, `application.post` (use the experiment-root `post:`), and `test:` (use `item:`).

To restrict execution, use `mas-lab benchmark run --scenario-id ID` or `--dataset-item ID`.
To attach an extra pipeline from the command line, use `--pipeline LEVEL[:PHASE]:REF`.

A step entry takes one of three forms:

| Form | Example |
| ------ | --------- |
| Library id | `{ id: analysis }` or a shorthand string in lists |
| File ref | `./pipelines/post-run.yaml` or `{ ref: ... }` |
| Inline steps | `{ steps: [{ name: s1, type: plot_trajectory, ... }] }` |

Full annotated example with one hook per level:

```yaml
experiment:
  name: topology-ablation
  model: any                    # shorthand for models.main; omitting it means any
  models:                       # optional slot map (main / summarizer / judge)
    main: any

  # Application: MAS + experiment-level post (gather, CI, figure)
  application:
    app: trip-planner
    configs_dir: ./overlays
    # Preferred for shared apps: library identifier + versioned app id:
    # app: example-library:my-app@v2
    # manifest: example-library:apps/my-app/v2/mas.yaml
  artifacts: {df: dataframe}
  post:
    - {name: gather-experiment, type: gather_level, in: df, out: df, depends_on: [gather-scenario]}

  # Scenario: overlay columns + gather
  scenarios:
    - id: linear
      overlays: {logic: [linear], control: [], infra: []}
  scenario:
    artifacts: {df: dataframe}
    post:
      - {name: gather-scenario, type: gather_level, in: df, out: df, depends_on: [gather-item]}

  # Item: dataset item + gather
  dataset:
    name: trip-planner-benchmark
    locator: samples
    # limit: 10   # optional: first N items; omit to run the full Dataset
  item:
    artifacts: {df: dataframe}
    post:
      - {name: gather-item, type: gather_level, in: df, out: df, depends_on: [extract-trace-stats]}

  # Run: one MAS execution; n_runs lives here
  run:
    n_runs: 5
    artifacts:
      trace: trace
      df: dataframe
    post:
      - name: extract-trace-stats
        type: extract_trace_stats
        in: trace
        out: df

  # Schedule: how the bench walks the matrix (last)
  schedule:
    parallel_scenarios: 4
    timeout: 300
    ordering: coverage
```

### Model slots (`models` / `model`)

Model slots are named model pins, not `$variables`.
Keys match Agent and MAS `spec.models[].id`, plus `judge`.
An agent that says `model: any` inherits the experiment value, then the MAS value, then the local `config.yaml` value.

Scalar `experiment.model` is shorthand for `models.main`. If both are set, `models.main` wins.

```yaml
experiment:
  models:
    main: gpt-4o                # turn default (fills Agent/MAS model: any)
    summarizer: gpt-4o-mini     # summary default when params.model is omitted
    judge: gpt-4o-mini          # MCE default; evaluation.model still wins
```

`evaluation.model` and `eval_mce.config.model` stay judge-only overrides.
`summarizer.params.model` stays a per-agent summary override.
Use overlays to change the graph (tools, pattern). Pin models here.
See [summarization.md](summarization.md) for the full chain.

---

## Fields you sometimes set

| Key | Default | Meaning |
| --- | --- | --- |
| `description` | none | Free text shown in reports. |
| `metadata` | none | Free-form reproducibility notes (model, dataset version, ...). Not interpreted. |
| `default_flavour` | `local` | Runtime flavour used when the CLI passes no `--flavour`. See [flavour.md](flavour.md). |
| `default_infra` | none | Infra file used by `service_start` / `service_stop` when the CLI passes no `--infra`. For those steps this is a `kind: ServiceBundle` under `<experiment_dir>/infra/` ([infra.md](infra.md)). LLM `--infra-ref` is a different flag. |
| `checkpoints` | one fresh start | Starting-state axis. See [Checkpoint axis](#checkpoint-axis). |
| `evaluation` | none | `method` (required when the block is present): `user_emulation`, `llm_judge`, `metrics`, or `trace_only` (collect trajectories without scoring). Optional `model` (judge) and free-form `config`. |
| `trace_cache_dir` | `$MAS_TRACE_CACHE`, then `$XDG_CACHE_HOME/mas/traces` | Trace-cache root for this experiment. The **trace cache** stores completed run traces, so a run with identical inputs reuses its trace. `--trace-cache` on the CLI wins. See [user-config.md](../user-config.md). |
| `artifacts`, `pre`, `post` | none | Experiment-level artifacts and hooks. See [Artifacts](#artifacts). |
| `scenario`, `item` | none | Level sections (`artifacts`, `pre`, `post`). |
| `ui` | none | Demo-UI hints (`port`, `mode`, `layout`, `node_positions`), same shape as in `lab-config.yaml`. `mas-lab benchmark run` ignores it. |

### Evaluation model (`evaluation.model`)

`eval_mce` is the pipeline step that scores a run with the Metrics Computation Engine (MCE). See [Tutorial 10](../tutorials/10-evaluation-metrics/README.md).
It picks its judge model in this order:

1. `eval_mce.config.model` on the step.
2. `experiment.evaluation.model`.
3. `experiment.models.judge`.
4. `models.main` / `experiment.model`.
5. The application's MAS or Agent `spec.models[]`.

The value `any` (or no value) means the spec pins no provider id. Local `config.yaml` `defaults.model` fills it.

```yaml
experiment:
  name: judge-example
  models:
    main: gpt-4o
    judge: gpt-4o-mini          # or evaluation.model: gpt-4o-mini
  application:
    manifest: ./mas.yaml
  evaluation:
    method: llm_judge
  run:
    post:
      - {name: eval-quality, type: eval_mce}   # one run per invocation, so run level
```

See [summarization.md](summarization.md#mce-judge-model) for the resolution order and logs.
A feature example (not a sample app) is in [library-eval/examples/mce/judge-override/](https://github.com/outshift-open/mas-lab/tree/main/library-eval/examples/mce/judge-override).

### Artifacts

`artifacts:` declares typed outputs (`trace`, `metrics`, `dataframe`, `plot`, ...) at each level.
Types come from library plugins of `type: artifact`.

- Short form names only the type: `metrics: metrics`.
- Long form `{type, path, validate}` overrides the path. With `validate: true` (default `false`), the loader checks the artifact against its type schema.

```yaml
run:
  artifacts:
    trace: { type: trace, path: "{run_dir}/traces/events.jsonl" }
    metrics: metrics
    df: { type: dataframe, path: "{level_dir}/data.csv" }
  post:
    - name: eval-quality
      type: eval_mce
      in: trace     # reads this level's `trace` artifact
      out: metrics  # writes this level's `metrics` artifact

item:
  artifacts:
    df: { type: dataframe, path: "{level_dir}/data.csv" }
  post:
    - name: gather-test
      type: gather_level
      in: df        # fans in every child `run`'s `df` instance
      out: df       # writes this level's own `df`
```

`path` is relative to the directory of the level that produces the artifact.
Placeholder segments such as `{run_dir}`, `{level_dir}`, or `{output_dir}` mark that root and are dropped.
So `{run_dir}/traces/events.jsonl` means `traces/events.jsonl` inside each run folder.
Without `path`, the file is `<name>.<format>` for the type.

When a step's `in:` names an artifact declared one level below its own scope, the executor resolves every child instance's file path.
It passes them as `config["artifact_paths"]` (fan-in).
`out:` only documents which of this level's artifacts the step produces.
Nothing enforces it beyond the step's own config (`output:` or `subdir:`, relative to the level folder).

Other step keys in level hooks:

- `name`: defaults to the type.
- `depends_on`.
- `config`.
- `concurrency`: max run-scope instances in flight (default `1`).
- `per_run` / `per_scenario`: legacy expansion flags. The step's level already decides how often it runs.

These are the only step keys the experiment schema accepts inline.
Scheduler keys such as `role`, `resource`, or `persist` belong in a standalone pipeline file referenced with `{ref:}`.
See [pipeline.md](pipeline.md#steps-types-and-artifacts).

### Checkpoint axis

`experiment.checkpoints` is an optional starting-state axis.
It is crossed with `scenarios`, dataset items, and `run.n_runs`. Omit it to keep the existing matrix.
The `none` entry is a fresh start. Put it beside saved states for a direct what-if comparison:

```yaml
experiment:
  checkpoints:
    - id: none
    - id: after-triage
      path: ./checkpoints/after-triage.checkpoint.json
```

Rules:

- Each entry needs a unique `id` made of alphanumerics, dashes, or underscores.
- Every entry except `none` needs a checkpoint `path`, resolved relative to the experiment YAML.
- A non-trivial axis overrides an item's own `inputs.checkpoint.load`. It emits a warning when both are set.
- With no axis, the item-level load applies directly.
- The run writes checkpoint ids and source paths to `results.csv` and `session_mappings.jsonl`, next to each run's session id.

See the [checkpoint-axis example](../schemas/examples/checkpoint-axis.yaml) and [Tutorial 11](../tutorials/11-sessions-and-recovery/README.md).
[checkpoint.md](checkpoint.md) describes the checkpoint files themselves.

---

## Advanced fields

### Design vs schedule {#design-vs-schedule}

The former `experiment.execution` block is split into three concerns:

- **design**: what you compare;
- **schedule**: how the bench walks the matrix;
- **bench emulation**: whether runs are live or recorded (mock, replay, trace cache).

The schema `$id` stays `experiment/v1`.
An `execution:` mapping still loads for one release, with an `experiment.execution` deprecation warning.
The loader maps its keys onto `schedule`, `design`, `bench_emulation` (from `execution.emulation` and `execution.faults`), and `replay`.
It uses `execution.n_runs` only when `run.n_runs` is absent.

Rule of thumb:

- If changing a field changes the label or meaning of a result cell, it is **design**.
- If it only changes how fast or how safely the same cells are collected, it is **schedule**.
- If it changes live-versus-recorded behaviour but stays fixed across the study, it is **bench emulation**.

| Concern | Question | In analysis design? | YAML home |
| --- | --- | --- | --- |
| **Design** | What conditions are we comparing? | **Yes** | `scenarios[]`, `design`, `dataset`, `evaluation`, `run.n_runs`, `checkpoints` |
| **Schedule** | How do we walk the matrix? | **No** | `schedule` (`ordering`, `parallel_scenarios`, `timeout`, `max_attempts`, `runner`, ...) |
| **Bench emulation** | Mock, replay, or trace-cache posture? | Only if it is an intentional factor | `bench_emulation` |
| **Deployment wiring** | Which LLM bundle or runtime profile? | **No** (unless a declared factor) | Workspace `infra_refs` / `runtime_refs`, `--infra-ref` / `--runtime-ref` |

When infra is a factor, use scenario `overlays.infra`.
The lab trace cache (`bench_emulation.runtime.cache`) is not the `RuntimeEngine` LLM response cache. See [runtime-engine.md](runtime-engine.md).
For offline LLM turns, use [llm_cache replay](llm-cache.md) through workspace `infra_refs`.

### `design`

```yaml
experiment:
  design:
    mode: cartesian          # cartesian | coupled | one_factor
    max_executions: 200      # fail if the planned grid is larger
```

The three modes pick cells from the grid of scenarios x dataset items.
`run.n_runs` always replicates each cell.
They are not a general factorial language.
Missing or unknown keys fail. They do not fall back to cartesian.

| Mode | Cells | Required keys |
| --- | --- | --- |
| `cartesian` | every scenario x every item | none |
| `coupled` | listed `(scenario, items)` pairs | `couplings: [{scenario: <id>, items: [<item id>, ...]}]` |
| `one_factor` | sweep one of those two axes; hold the other | `vary` (`axis` + `values`); optional `pin_scenarios` / `pin_items` (omit to keep every scenario or item) |

`max_executions` is optional. It fails the plan when scenarios x items x `n_runs` exceeds it.
`couplings` is allowed only with `coupled`.
`vary`, `pin_scenarios`, and `pin_items` are allowed only with `one_factor`.

```yaml
experiment:
  design:
    mode: one_factor
    pin_scenarios: [baseline]
    vary:
      axis: item             # scenario | item
      values: [item-1, item-2]
```

Other study axes stay outside this enum:

- **Overlay or infra as a factor**: one `scenarios[]` row per column (`overlays.logic` / `control` / `infra`). Hold infra constant with workspace `infra_refs` and `bench_emulation`, not a per-cell cache flip.
- **Model as a factor**: two experiments with different `models.main`, or two scenarios whose overlays patch the model. `experiment.models` pins the model for the whole study.
- **Starting state**: `experiment.checkpoints` is crossed after the plan. It is always cartesian on that axis today.
- **Smoke tests, not a pin**: `dataset.limit` and `design.max_executions`.

Replication is design, not a retry knob. Set it with `run.n_runs` only.

### `schedule`

```yaml
experiment:
  schedule:
    parallel_scenarios: 4
    timeout: 300
    pause_between_runs: 1.0
    ordering: coverage       # coverage | depth  (legacy alias: strategy)
    max_attempts: 3          # re-issue a failed slot (independent of n_runs)
    retry_backoff_s: 2.0     # delay before the first re-issue; doubles each time
    runner: native           # optional override of inferred adapter
    reset_state: false
```

| Key | Default | Meaning |
| --- | --- | --- |
| `parallel_scenarios` | `4` | Max concurrent MAS runs. |
| `timeout` | `300` | Per-run timeout, seconds. |
| `pause_between_runs` | `1.0` | Sleep between runs, seconds. |
| `ordering` | `coverage` | `coverage`: one run of every cell before any second run (breadth-first). `depth`: finish all `n_runs` of a cell first. `strategy` is a deprecated alias. |
| `max_attempts` | `3` | Tries per failed scenario x item x run, including the first. `1` disables re-issue. |
| `retry_backoff_s` | `2.0` | Base delay before the first re-issue; doubles on each later one. |
| `runner` | `native` | Lab runner (adapter) id from the `mas.lab.runners` entry point group. When omitted, inferred from the application's `spec.framework.default_adapter` (MAS) or `spec.framework_adapter` (agent). |
| `reset_state` | `false` | Reset emulation and runtime state between runs. |

`--strategy` on `mas-lab benchmark run` overrides `schedule.ordering`.

### `bench_emulation`

`bench_emulation` sets the study-wide trace-cache and pipeline-executor posture.
Hold it constant across cells unless it is a declared factor.

```yaml
experiment:
  name: my-bench
  application:
    app: trip-planner
    configs_dir: ./overlays
  run:
    n_runs: 1
  bench_emulation:
    runtime:
      cache: disabled
```

| Key | Meaning |
| --- | --- |
| `runtime.cache` | Lab **trace cache** policy. `content-addressed` (default): skip a run whose fingerprint (resolved manifest, run input, item id, run index, flavour, infra) already has a recorded trace. `disabled`: always execute. `forced`: fail on a cache miss (fully offline). |
| `pipeline` | Pipeline executor settings: `store` (artefact store root; default next to the trace cache), `max_active` (global cap on in-flight pipeline nodes, default `8`), `streams.transport` (`inproc`, `log`, or `websocket`; default `inproc`). `engine` and `shadow` are ignored. |
| `infra` | Per-resource mode (`llm`, `tools`, `memory`, `embeddings`, `state`; default `live`). Only `live` works with the shipped packages. See the note below. |
| `faults`, `intercept` | See the note below. |

!!! note "Not used yet"
    `bench_emulation.infra` values other than `live` need a resource-emulation plugin that this release does not include.
    `faults` and `intercept` are reserved for fault-injection and interception plugins that this release does not include. Leave them unset.

Implementation types: `ExperimentScheduleSpec`, `ExperimentDesignSpec`, and `EmulationSpec` (`mas.lab.lab.config.execution`).
`MASExecutionSpec` is a compatibility view synthesized while both formats load.
Migrate in-repo YAML with `scripts/migrate_experiment_execution.py`.

### `replay`

`replay` holds multi-turn replay options: `mode` (`scripted` or `cache-and-diverge`), `hitl_source` (`dataset` or `recording`), `recording_path`, and `diverge_from_cache`.

!!! note "Not used yet"
    The loader parses and validates `replay`. The native batch runner does not act on it.

### `output_schema:`

`output_schema` is **deprecated**. It still loads, and `mas-lab validate` warns.
Prefer the `df_schema` of plot steps and the files those steps write.
The block lists files that must exist in the output directory and, per file, the columns that must be present:

```yaml
experiment:
  output_schema:
    required_files:
      - "results/ci_summary.csv"
    required_columns:
      results/ci_summary.csv: [scenario, metric, mean, ci_low, ci_high]
```

Declaring the block does not run the check.
Add a `validate_outputs` step, typically last in the experiment-root `post:`, with `config: {schema: <the output_schema dict above>}`.
The step only warns unless `config.warn_only: false`.
See [pipeline-steps.md](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/pipeline-steps.md).

### Deprecated and removed keys

`applications`, `mas`, `execution`, and `test` are deprecated aliases. They still load with a warning.
`experiment.applications` takes a one-item list. `lab.mas` in `lab-config.yaml` is also deprecated.
Rewrite the binding like this:

```yaml
# before
mas:
  manifest: ../../apps/my-app/mas.yaml
  configs_dir: overlays

# after
application:
  app: example-library:my-app@v1
  configs_dir: overlays
```

The loader rejects the removed keys `pipeline_bind`, `pipeline`, `plots`, `flavours`, and `output_dir`.

---

## Common mistakes

- **A bare list under `overlays`.** `overlays` must be a mapping with `logic`, `control`, and `infra`. Use `[]` for an empty stack.
- **A `pipelines:` key.** It does not exist. Attach steps with `pre:` / `post:` on a level.
- **Both `model` and `models.main`.** `models.main` wins. Set one.
- **`couplings` with `cartesian`.** `couplings` is valid only with `mode: coupled`. `vary`, `pin_scenarios`, and `pin_items` are valid only with `one_factor`.
- **Treating `n_runs` as a retry count.** It replicates each cell for variance. Use `schedule.max_attempts` to retry a failed run.
- **Expecting `output_schema` to run by itself.** Add a `validate_outputs` step.
- **Confusing the two caches.** `bench_emulation.runtime.cache` is the lab trace cache. It is not the `RuntimeEngine` LLM response cache.
- **A reduced copy of a dataset for smoke runs.** Use `dataset.limit`.
- **Expecting `out:` to be enforced.** It documents the artifact a step writes and nothing more.
- **Setting both `path` and `name` on `dataset`.** `path` wins and `name` is ignored.

---

## See also

- [lab.md](lab.md)
- [dataset.md](dataset.md), [overlay.md](overlay.md), [flavour.md](flavour.md)
- [pipeline.md](pipeline.md)
- [summarization.md](summarization.md): judge model and conversation summarization
- [runtime-engine.md](runtime-engine.md)
- [Tutorial 03](../tutorials/03-experiments-and-analysis/README.md)
