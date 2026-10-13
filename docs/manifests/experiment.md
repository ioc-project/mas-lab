<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Experiment manifest (`experiment:`)

**Package:** `mas-lab-bench` · **Schema:** `experiment.schema.yaml`

An **experiment** manifest tells `mas-lab benchmark run` what to execute: which **MAS** or
**agent**, which **scenarios** (each with **overlays**), which **dataset** items, how many
**runs** per item (`n_runs`), and which **pipeline** steps build `results/` afterward.
Defines **benchmark** metadata, execution modes, lifecycle levels, and **pipeline** hooks.

Benchmark runs accept repeatable schema-backed CLI overrides. The `experiment:`
root is patched before validation, scenario expansion, and dataset loading:

```bash
mas-lab benchmark run experiment.yaml \
  --override 'experiment:experiment.run.n_runs=1' \
  --override 'experiment:experiment.dataset.limit=2'
```

Reusable selector and wildcard groups belong in an Overlay manifest under
`spec.overrides`; see the [Overlay manifest reference](overlay.md) for the
target, patch, precedence, and validation contract.

---

## Four layers (matches CLI `--depth exp|scenario|item|run`)

There is **no** `pipelines:` key. Hooks live on the same objects the CLI
already names:

| YAML | CLI | Scope | Typical post |
| ------ | ----- | ------- | -------------- |
| `post:` (experiment root) | `--depth exp` | Whole experiment | `gather_level`, CI, plots |
| `scenario:` | `--scenario` / `--depth scenario` | One overlay column | Gather item frames |
| `item:` | `--item` / `--depth item` | One dataset item (all runs) | Gather run frames |
| `run:` | `--depth run` | Single run index | `eval_mce`, `metrics_to_dataframe`, `extract_trace_stats` |

`application:` is the **MAS binding** (`app` / `manifest` / `configs_dir`), not a pipeline level.
Deprecated aliases still load with a warning: `applications:`, `application.post`, `test:`.

Each level supports `pre:` and `post:` as **lists of steps** (0..N).

---

## Core fields

```yaml
experiment:
  name: topology-ablation
  model: any                    # shorthand for models.main; omit ≡ any
  models:                       # optional slot map (main / summarizer / judge)
    main: any

  # ── Application ── MAS + experiment-level post (gather → CI → figure)
  application:
    app: trip-planner
    configs_dir: ./overlays
    # Preferred for shared apps — library identifier + versioned app id:
    # app: example-library:my-app@v2
    # manifest: example-library:apps/my-app/v2/mas.yaml
  artifacts: {df: dataframe}
  post:
    - {name: gather-experiment, type: gather_level, in: df, out: df, depends_on: [gather-scenario]}

  # ── Scenario ── overlay columns + gather
  scenarios:
    - id: linear
      overlays: {logic: [linear], control: [], infra: []}
  scenario:
    artifacts: {df: dataframe}
    post:
      - {name: gather-scenario, type: gather_level, in: df, out: df, depends_on: [gather-item]}

  # ── Item ── dataset item + gather
  dataset:
    name: trip-planner-benchmark
    locator: samples
    # limit: 10   # optional: first N items; omit to run the full Dataset
  item:
    artifacts: {df: dataframe}
    post:
      - {name: gather-item, type: gather_level, in: df, out: df, depends_on: [extract-trace-stats]}

  # ── Run ── one MAS execution; n_runs lives here
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

  # ── Schedule ── how the bench walks the matrix (last)
  schedule:
    parallel_scenarios: 4
    timeout: 300
    ordering: coverage
```

---

## Model slots (`models` / `model`)

Named slots, not `$variables`. Keys match Agent/MAS `spec.models[].id`, plus
`judge`. Agents that say `model: any` inherit the experiment (then MAS, then
local `config.yaml`). Scalar `experiment.model` is **shorthand for
`models.main`**; `models.main` wins when both are set.

```yaml
experiment:
  models:
    main: gpt-4o                # turn default (fills Agent/MAS model: any)
    summarizer: gpt-4o-mini     # summary default when params.model omitted
    judge: gpt-4o-mini          # MCE default; evaluation.model still wins
```

`evaluation.model` and `eval_mce.config.model` remain judge-only overrides.
`summarizer.params.model` remains a per-agent summary override. Overlay the
graph (tools, pattern); pin models here.

See [summarization.md](summarization.md) for the full chain.

---

## Evaluation model (`evaluation.model`)

`eval_mce` defaults to `experiment.models.judge`, then `models.main` /
`experiment.model`, then the application MAS/Agent `spec.models[]`. `any`
(or omitted) means the spec does not pin a provider id — local `config.yaml`
`defaults.model` fills it. Override the judge with `experiment.evaluation.model`;
a per-step `eval_mce.config.model` still wins.

```yaml
experiment:
  models:
    main: gpt-4o
    judge: gpt-4o-mini          # or evaluation.model: gpt-4o-mini
  applications:
    - manifest: ./mas.yaml
  evaluation:
    method: llm_judge
  application:
    post:
      - {type: eval_mce, depends_on: [extract_trajectories]}
```

See [summarization.md](summarization.md#mce-judge-model) for resolution order
and logs. Feature example (not a sample app):
[library-eval/examples/mce/judge-override/](../../library-eval/examples/mce/judge-override/).

---

## Applications

`experiment.applications` is the MAS pointer (`app:` catalog id or `manifest:` path).

```yaml
applications:
  - app: example-library:my-app@v1
    configs_dir: ./overlays
```

`experiment.mas` (and `lab.mas` in `lab-config.yaml`) is a **deprecated** alias of
`applications[0]`. Loaders still accept it and emit a warning. Rewrite:

```yaml
# before
mas:
  manifest: ../../apps/my-app/mas.yaml
  configs_dir: overlays

# after
applications:
  - app: example-library:my-app@v1
    configs_dir: overlays
```

Relative `manifest:` paths still work; prefer `app: library:id@version`.

---

## Pipeline reference forms

| Form | Example |
| ------ | --------- |
| Library id | `{ id: analysis }` or shorthand string in lists |
| File ref | `./pipelines/post-run.yaml` or `{ ref: ... }` |
| Inline steps | `{ steps: [{ name: s1, type: plot_trajectory, ... }] }` |

---

## Checkpoint axis

`experiment.checkpoints` is an optional starting-state axis crossed with
`scenarios`, dataset items, and `run.n_runs`. Omit it to preserve the existing
matrix. The `none` entry is a fresh start and can sit beside saved states for a
direct what-if comparison:

```yaml
experiment:
  checkpoints:
    - id: none
    - id: after-triage
      path: ./checkpoints/after-triage.checkpoint.json
```

Each entry needs a unique alphanumeric, dash, or underscore `id`. Every entry
except `none` needs a checkpoint `path`, resolved relative to the experiment
YAML. A non-trivial axis overrides an item's own `inputs.checkpoint.load` and
emits a warning when both are set. With no axis, item-level loading is used
directly. Checkpoint IDs and source paths are written to `results.csv` and
`session_mappings.jsonl` alongside each run's session ID.

See the [checkpoint-axis example](../schemas/examples/checkpoint-axis.yaml)
and [Tutorial 6](../tutorials/06-sessions-and-recovery/README.md).

---

## Artifacts

`artifacts:` declares typed outputs (`trace`, `metrics`, `dataframe`, `plot`, …) at each
level. Short form names just the type (`metrics: metrics`); long form overrides the path
template and turns on schema validation:

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
      out: df        # writes this level's own `df`
```

A step's `in:` names an artifact declared at the level **below** its own scope — the
executor resolves every child instance's file path and passes them as
`config["artifact_paths"]`. `out:` just documents which of this level's own declared
artifacts the step produces; nothing enforces it beyond the step's own config
(`output:`/`output_dir:`).

---

## `output_schema:`

Optional fail-fast check on the final output directory — declares files that must
exist and, per file, columns that must be present:

```yaml
experiment:
  output_schema:
    required_files:
      - "results/ci_summary.csv"
    required_columns:
      results/ci_summary.csv: [scenario, metric, mean, ci_low, ci_high]
```

Declaring it doesn't run the check by itself — add a `validate_outputs` step
(typically last in `application: post:`) with `config: {schema: <the output_schema
dict above>}`; see [pipeline-steps.md](../../lab/docs/pipeline-steps.md).

---

## Design vs schedule {#design-vs-schedule}

The former `experiment.execution` grab-bag is split into three concerns. Schema
`$id` stays `experiment/v1`. A top-level `execution:` mapping still loads for one
release and emits `experiment.execution` via `mas.lab.deprecations.warn_deprecated`.

Rule of thumb: if changing a field would change the **label or meaning** of a
result cell, it is **design**. If it only changes how fast or safely the same
cells are collected, it is **schedule**. If it changes live-vs-recorded behavior
but is held fixed across the study, it is **bench emulation**.

| Concern | Question | In analysis design? | YAML home |
| --- | --- | --- | --- |
| **Design** | What conditions are we comparing? | **Yes** | `scenarios[]`, `design`, `dataset`, `evaluation`, `run.n_runs`, `checkpoints` |
| **Schedule** | How do we walk the matrix? | **No** | `schedule` (`ordering`, `parallel_scenarios`, `timeout`, `max_attempts`, `runner`, …) |
| **Bench emulation** | Mock/replay/trace-cache posture? | Only if it is an intentional factor | `bench_emulation` |
| **Deployment wiring** | Which LLM bundle / runtime profile? | **No** (unless a declared factor) | Workspace `infra_refs` / `runtime_refs`, `--infra-ref` / `--runtime-ref` |

`emulation.infra.llm: mock` remains a first-class `bench_emulation` field for a
study-wide constant mock. When infra **is** a factor, use scenario `overlays.infra`.
The lab trace cache (`bench_emulation.runtime.cache`) is **not** the
`RuntimeEngine` LLM response cache — see [runtime-engine.md](runtime-engine.md).

### `design`

```yaml
experiment:
  design:
    mode: cartesian          # cartesian | coupled | one_factor
    max_executions: 200      # fail if the planned grid is larger
```

These three modes only pick cells from **scenarios × dataset items**
(`run.n_runs` always replicates each cell). They are not a general
factorial language. Missing or unknown keys fail; they do not fall back
to cartesian.

| Mode | Cells | Required keys |
| --- | --- | --- |
| `cartesian` | every scenario × every item | none |
| `coupled` | listed `(scenario, items)` pairs | `couplings` |
| `one_factor` | sweep one of those two axes; hold the other | `vary` (`axis` + `values`) |

```yaml
experiment:
  design:
    mode: one_factor
    pin_scenarios: [baseline]
    vary:
      axis: item             # scenario | item
      values: [item-1, item-2]
```

Other study axes stay outside this enum until they are folded into a
scenario overlay, or declared separately:

- **Overlay / infra as a factor** — one `scenarios[]` row per column
  (`overlays.logic` / `control` / `infra`). Hold infra constant with
  workspace `infra_refs` and `bench_emulation`, not a per-cell cache flip.
- **Model as a factor** — two experiments with different `models.main`,
  or two scenarios whose overlays patch the model. Study-wide pin:
  `experiment.models`.
- **Starting state** — `experiment.checkpoints` is crossed after the
  plan (always cartesian on that axis today).
- **Smoke, not a pin** — `dataset.limit` and `design.max_executions`.

Replication is **design**, not a retry knob: `run.n_runs` only.

### `schedule`

```yaml
experiment:
  schedule:
    parallel_scenarios: 4
    timeout: 300
    pause_between_runs: 1.0
    ordering: coverage       # coverage | depth  (legacy alias: strategy)
    max_attempts: 3          # re-issue a failed slot (independent of n_runs)
    runner: native           # optional override of inferred adapter
    reset_state: false
```

`--strategy` on `mas-lab benchmark run` still exists; it writes/overrides
`schedule.ordering`.

### `bench_emulation`

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

Implementation types: `ExperimentScheduleSpec`, `ExperimentDesignSpec`,
`EmulationSpec` (`mas.lab.lab.config.execution`). `MASExecutionSpec` is a
compatibility view synthesized during dual-read.

Migrate in-repo YAML with `scripts/migrate_experiment_execution.py`.

---

## Dataset

`experiment.dataset.name` is a catalog id (`locator: samples`) or a lab-local
`datasets/<name>.yaml`. Nested experiments (`01-foo/experiment.yaml`) resolve
the lab root `datasets/` automatically.

`dataset.limit` takes the first N items. Use that for smoke and CI. Do not
check in a reduced copy of the same pack (`*-100.yaml`).

```yaml
dataset:
  name: trip-planner-benchmark
  locator: samples
  limit: 5
```

---

## See also

- [lab.md](lab.md)
- [pipeline.md](pipeline.md)
- [summarization.md](summarization.md) — judge model + conversation summarization
- [runtime-engine.md](runtime-engine.md)
- [Tutorial 03](../tutorials/03-experiments-and-analysis/README.md)
