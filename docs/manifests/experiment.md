<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Experiment manifest (`experiment:`)

**Package:** `lab/components/bench` · **Schema:** `experiment.schema.yaml`

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

`--control REF[@SELECTOR]` attaches a `kind: Control` file the same way `mas-ctl chat` does.
A scenario's `overlays.control` is the YAML bucket for that path. Quote a selector so the
shell does not expand `*`.

Put reusable selector and wildcard groups in an Overlay manifest under `spec.overrides`.
The [Overlay manifest reference](overlay.md) defines the target, patch, precedence, and validation rules.

## Contents

- [Fields you always set](#fields-you-always-set): [top-level keys](#top-level-keys), [applications](#applications), [scenarios](#scenarios), [dataset](#dataset), [run levels](#run-levels-and-n_runs), [model slots](#model-slots-models-model)
- [Fields you sometimes set](#fields-you-sometimes-set): [evaluation model](#evaluation-model-evaluationmodel), [level hooks and artifacts](#artifacts), [checkpoint axis](#checkpoint-axis), [session checkpoints](#session-checkpoints), [environment](#environment)
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

### Run-wide overrides

Use `overrides` for a change that belongs to the whole lab and to no scenario: an endpoint setting, a model, a request field.

```yaml
experiment:
  overrides:
    # a LiteLLM gateway caches identical requests; ask it not to, for every worker
    - .mas.agent[worker].spec.models[0].extra.cache.no-cache=true
    - .infra.spec.models.defaults.llm=qwen2.5:7b-instruct
```

The same strings work on the command line: `mas-lab benchmark run experiment.yaml --override '.infra.spec.models.defaults.llm=gemma4'`. A bad path fails before any run starts. Overrides apply to runs of a MAS; a standalone agent run ignores `.mas` and `.infra` paths.

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
| `overlays` | no | Three stacks applied in order `logic`, `control`, `infra`. `logic` and `infra` are Overlay patches. `control` attaches `kind: Control` files the same way `--control` does. Each entry is an id (resolved in `configs_dir`) or `{ref: path}`. When `overlays` is present, all three keys are required; use `[]` for an empty stack. |
| `description` | no | Label shown in reports. |
| `tags` | no | Grouping and filtering labels. |
| `flavour` | no | Per-scenario flavour name or path. A **flavour** is the glue that links the logic spec to the infra (its `bind`); see [flavour.md](flavour.md). |
| `infra_refs` | no | Infra files (`ToolServerRegistry`, `Identity`, ...) this scenario's runs load instead of the workspace `infra_refs`; a relative path is relative to the experiment file. Use it when the scenarios differ in their infra rows, for example a tool server with and without an identity layer. A scenario without it keeps the workspace files. |

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
    plugin: coverage
```

### Model slots (`models` / `model`)

Model slots are named model pins, not `$variables`.
Keys match Agent and MAS `spec.models[].id`, plus `judge`.
An agent that says `model: any` inherits through the [MAS model resolution](mas.md#modelentry) chain (MAS, experiment, infra `models.defaults.llm`, workspace `config.yaml`).

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
| `overrides` | none | List of `PATH=VALUE` strings that every run of every scenario applies, in the grammar of `mas-ctl run-mas --override`; a path starts with `.mas`, `.agent` or `.infra`. They apply before a scenario's overlays, so a scenario can supersede them. `mas-lab benchmark run --override '.infra....=V'` adds to the list; a path under `experiment:` still patches this file. See [Run-wide overrides](#run-wide-overrides). |
| `default_infra` | none | Infra file used by `service_start` / `service_stop` when the CLI passes no `--infra`. For those steps this is a `kind: ServiceBundle` under `<experiment_dir>/infra/` ([infra.md](infra.md)). LLM `--infra-ref` is a different flag. |
| `checkpoints` | one fresh start | Starting-state axis. See [Checkpoint axis](#checkpoint-axis). |
| `evaluation` | none | `method` (required when the block is present): `user_emulation`, `llm_judge`, `metrics`, or `trace_only` (collect trajectories without scoring). Optional `model` (judge) and free-form `config`. |
| `trace_cache_dir` | `$MAS_TRACE_CACHE`, then `$XDG_CACHE_HOME/mas/traces` | Trace-cache root for this experiment. The **trace cache** stores completed run traces, so a run with identical inputs reuses its trace. `--trace-cache` on the CLI wins. See [user-config.md](../user-config.md). |
| `trace_cache_portable` | `false` | Leave the resolved LLM infra (endpoint, model mapping) out of the trace-cache key. Set it on experiments whose cache you publish: a reader with another endpoint then hits the same entries. Agent model names still enter the key. |
| `artifacts`, `pre`, `post` | none | Experiment-level artifacts and hooks. See [Artifacts](#artifacts). |
| `scenario`, `item` | none | Level sections (`artifacts`, `pre`, `post`). |
| `environment` | none | World around every run: fixtures, clock, timers, triggers. Dataset items overlay timers and triggers; they cannot declare fixtures. See [Environment](#environment). |
| `ui` | none | Demo-UI hints (`port`, `mode`, `layout`, `node_positions`), same shape as in `lab-config.yaml`. `mas-lab benchmark run` ignores it. |

### Evaluation model (`evaluation.model`)

`eval_mce` is the pipeline step that scores a run with the Metrics Computation Engine (MCE). See [Tutorial 10](../tutorials/10-evaluation-metrics/README.md).
It picks its judge model in this order:

1. `eval_mce.config.model` on the step.
2. `experiment.evaluation.model`.
3. `experiment.models.judge`.
4. `models.main` / `experiment.model`. The env override
   (`MAS_CTL_MODEL` / `MAS_LLM_MODEL`) fills this slot when
   `models.judge` is omitted.
5. The application's MAS or Agent `spec.models[]`.

The value `any` (or no value) means the spec pins no provider id. Local `config.yaml` `defaults.model` fills it.
The judge model name is routed through workspace `LLMProxy` leaves the same way as the agent turn model — give eval a different model id when it should use a different leaf. See [Several LLMProxy refs](infra.md#several-llmproxy-refs).

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
A feature example (not a sample app) is in [library-eval/examples/mce/judge-override/](https://github.com/ioc-project/mas-lab/tree/main/library-eval/examples/mce/judge-override).

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

### Session checkpoints

`experiment.session_checkpoints` is a catalog of persist points produced
during a run, not a starting-state axis. Each entry is compiled to an
environment trigger that snapshots and persists at a kernel `when`
(pause to write, then resume so the producer continues). A scenario
that lists `produce_checkpoints` publishes those files under
`<output>/checkpoints/{item}/r{n}/`. A scenario that lists
`depends_on` and `resume_from` waits for that file, then loads it as
`inputs.checkpoint.load` when the session starts. The wait happens
before a `parallel_scenarios` slot is taken. Two producers of the same
id are refused.

```yaml
experiment:
  session_checkpoints:
    - id: after-pay-2
      when: {"on": tool_result, tool: pay_fare, occurrence: 2}
  scenarios:
    - id: producer
      overlays: {logic: [], control: [], infra: []}
      produce_checkpoints: [after-pay-2]
    - id: fork
      overlays: {logic: [], control: [], infra: []}
      depends_on: [producer]
      resume_from: after-pay-2
```

Omit `scenarios[].overlays` and the loader infers a file named after the
scenario id. Write empty lists when there is no overlay.

### Environment

`experiment.environment` (and `scenarios[].environment`) start the world
every run shares: fixtures, the clock kind, shared timers and triggers.
A dataset item may overlay timers and triggers on that world. It cannot
declare fixtures.

This is complementary to the slots you already have. It does not replace
them.

| Slot | Lives on | Role |
| --- | --- | --- |
| `environment` | experiment / scenario | Fixtures and the shared clock |
| `environment` | dataset item | Per-case triggers and timers |
| `inputs.memory_seeds` | dataset item | Facts the agent already knows |
| `inputs.tool_fixtures` | dataset item | Mock payloads a tool reads |
| `service_start` | level `pre:` | Pipeline infra for that level |
| Flavour / Control / Infra | overlay stack | How the runtime is deployed |

Merge order is experiment → scenario → item. Field reference:
[environment.md](environment.md). Dataset overlay:
[dataset.md §10](dataset.md#10-world-around-the-item-environment).
Worked example: [Tutorial 15](../tutorials/15-world-events-and-fixtures/README.md).

---

## Advanced fields

### Design vs schedule {#design-vs-schedule}

The former `experiment.execution` block is split into three concerns:

- **design**: what you compare;
- **schedule**: how the bench walks the matrix;
- **bench emulation**: whether runs are live or recorded (mock, replay, trace cache).

The schema `$id` stays `experiment/v1`.
An `execution:` mapping still loads for one release, with an `experiment.execution` deprecation warning.
The loader maps its keys onto `schedule`, `design`, `executor`, `bench_emulation` (from `execution.emulation` and `execution.faults`), and `replay`.
It uses `execution.n_runs` only when `run.n_runs` is absent.

Rule of thumb:

- If changing a field changes the label or meaning of a result run, it is **design**.
- If it only changes how fast or how safely the same runs are collected, it is **schedule** or **executor**.
- If it changes live-versus-recorded behaviour but stays fixed across the study, it is **bench emulation**.

| Concern | Question | In analysis design? | YAML home |
| --- | --- | --- | --- |
| **Design** | What conditions are we comparing? | **Yes** | `scenarios[]`, `design`, `dataset`, `evaluation`, `run.n_runs`, `checkpoints` |
| **Schedule** | In what order, and how many at once? | **No** | `schedule` (`plugin`, `parallel_scenarios`, `pause_between_runs`) |
| **Executor** | How is a run run once? | **No** | `executor` (`plugin`, `timeout`, `worker_processes`, `runner`) |
| **Retry** | Does a failed run run again? | **No** | `retry` (`plugin`, `params.max_attempts`, `params.backoff_s`) |
| **Analyzer** | Who reads the results tree? | **No** | `analyzer` (`plugin`, `params`) |
| **Bench emulation** | Mock, replay, or trace-cache posture? | Only if it is an intentional factor | `bench_emulation` |
| **Deployment wiring** | Which LLM bundle or runtime profile? | **No** (unless a declared factor) | Workspace `infra_refs` / `runtime_refs`, `--infra-ref` / `--runtime-ref` |

When infra is a factor, use scenario `overlays.infra`.
The lab trace cache (`bench_emulation.runtime.cache`) is not the `RuntimeEngine` LLM response cache. See [runtime-engine.md](runtime-engine.md).
For offline LLM turns, use [llm_cache replay](llm-cache.md) through workspace `infra_refs`.

### `design`

```yaml
experiment:
  design:
    plugin: cartesian        # registered scenario_generator id
    max_executions: 200      # fail if the planned grid is larger
    params: {}               # plugin attributes
```

`plugin` names a registered `scenario_generator`. Omit it and `cartesian` is used.
`run.n_runs` always replicates each run.
Missing or unknown plugin ids fail at config load with the registered list.
Params are validated by the plugin before any run. They are not a second cartesian language in the JSON schema.

| Plugin | Runs | Params |
| --- | --- | --- |
| `cartesian` | every scenario x every item | none |
| `coupled` | listed `(scenario, items)` pairs | `couplings: [{scenario: <id>, items: [<item id>, ...]}]` |
| `one_factor` | sweep one of those two axes; hold the other | `vary` (`axis` + `values`); optional `pin_scenarios` / `pin_items` (omit to keep every scenario or item) |
| `factorial` | product of named factors, selecting among declared scenario ids | `factors: {name: [values, ...]}`; optional `exclude` (scenario ids or factor maps) |

`max_executions` is a core key on `design`. It fails the plan when the generator's grid exceeds it.
Builtin generators take the old `couplings` / `vary` / `pin_*` keys as `params`.
`design.mode` and those keys at the top of `design` still load for one release, with an `experiment.design.mode` deprecation warning, and are rewritten to `plugin` + `params`. Giving both spellings is an error.

Write a generator: [scenario-generator.md](scenario-generator.md).

```yaml
experiment:
  design:
    plugin: one_factor
    params:
      pin_scenarios: [baseline]
      vary:
        axis: item             # scenario | item
        values: [item-1, item-2]
```

Other study axes stay outside this enum:

- **Overlay or infra as a factor**: one `scenarios[]` row per column (`overlays.logic` / `control` / `infra`). Hold infra constant with workspace `infra_refs` and `bench_emulation`, not a per-run cache flip.
- **Model as a factor**: two experiments with different `models.main`, or two scenarios whose overlays patch the model. `experiment.models` pins the model for the whole study.
- **Starting state**: `experiment.checkpoints` is crossed after the plan. It is always cartesian on that axis today.
- **Smoke tests, not a pin**: `dataset.limit` and `design.max_executions`.

Replication is design, not a retry knob. Set it with `run.n_runs` only.

### `schedule`

```yaml
experiment:
  schedule:
    plugin: coverage         # registered scheduler id; omit for the generator default
    params: {}
    parallel_scenarios: 4
    pause_between_runs: 1.0
```

`plugin` names a registered `scheduler`. Omit it and the generator's `default_scheduler` is used (`coverage` for cartesian, `as_generated` for coupled and one_factor).

| Plugin | Order |
| --- | --- |
| `coverage` | one run of every run before any second run (run-major) |
| `depth` | finish all `n_runs` of a run first (scenario-major) |
| `as_generated` | keep the generator's run order |

| Key | Default | Meaning |
| --- | --- | --- |
| `plugin` | generator default | Registered scheduler id. |
| `params` | `{}` | Scheduler attributes, validated at config load. |
| `parallel_scenarios` | `4` | Max concurrent MAS runs. |
| `pause_between_runs` | `1.0` | Sleep between runs, seconds. |

`schedule.ordering` and `schedule.strategy` still load for one release, with an `experiment.schedule.ordering` deprecation warning, and are rewritten to `schedule.plugin`. Giving both spellings is an error.

`--scheduler` on `mas-lab benchmark run` overrides `schedule.plugin`.

Write a scheduler: [scheduler.md](scheduler.md).

### `executor`

```yaml
experiment:
  executor:
    plugin: native           # registered executor id; omit for native
    params: {}
    timeout: 300
    runner: native           # optional override of inferred adapter
    reset_state: false
    worker_processes: 1      # 1 = threads in this process
```

`plugin` names a registered `executor`. The batch host still owns cache hits, resume, and `result.json`. The plugin runs one MAS invoke once; retry is the `retry` block. `runner` is the application runner id, not the executor plugin id.

| Key | Default | Meaning |
| --- | --- | --- |
| `plugin` | `native` | Registered executor id. |
| `params` | `{}` | Executor attributes, validated at config load. `native` accepts none. |
| `timeout` | `300` | Per-run timeout, seconds (worker pool). |
| `runner` | `native` | Lab runner (adapter) id from the `mas.lab.runners` entry point group. When omitted, inferred from the application's `spec.framework.default_adapter` (MAS) or `spec.framework_adapter` (agent). |
| `reset_state` | `false` | Reset emulation and runtime state between runs. |
| `worker_processes` | `1` | Process workers for MAS runs. `1` keeps today's threads. The batch refuses a count above the machine's cores when it starts (the file itself parses anywhere). With more than 1, every pipeline step must declare `worker_safe` (shipped library steps declare it; a test reads their sources for process patches). Workers start with `spawn` and rebuild their own run context from a plain job. |
| `worker_max_runs` | (off) | Recycle each worker after this many runs. Omit to keep the worker for the batch. |

Timeout, workers, and runner still load from `schedule` for one release, with an `experiment.schedule.executor` deprecation warning, and are rewritten onto `executor`. Giving the same host key on both blocks is an error.

### `retry`

```yaml
experiment:
  retry:
    plugin: native           # registered retrier id; omit for native
    params:
      max_attempts: 3        # total tries across rounds, the first included; 1 disables retry
      backoff_s: 2.0         # delay before the first re-issue; doubles each time
```

A retrier decides whether a failed run runs again. `max_attempts` / `retry_backoff_s` on the old `execution` / `schedule` blocks load into `retry.params` with a deprecation warning. Write a retrier: [retrier.md](retrier.md). Run rounds in CI: [ci-sharding.md](ci-sharding.md).

### `analyzer`

```yaml
experiment:
  analyzer:
    plugin: native           # registered analyzer id; omit for native (a completeness summary)
```

An analyzer reads a results tree and never runs a run. Write one: [analyzer.md](analyzer.md).

Write an executor: [executor.md](executor.md).

How wall time scales with `parallel_scenarios` and `worker_processes`: [Lab run throughput](../references/lab-throughput.md).

### `bench_emulation`

`bench_emulation` sets the study-wide trace-cache and pipeline-executor posture.
Hold it constant across runs unless it is a declared factor.

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
See [pipeline-steps.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/pipeline-steps.md).

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
- **`couplings` with `cartesian`.** `couplings` is a `coupled` param. `vary`, `pin_scenarios`, and `pin_items` are `one_factor` params.
- **Treating `n_runs` as a retry count.** It replicates each run for variance. Use `retry.params.max_attempts` to retry a failed run.
- **Expecting `output_schema` to run by itself.** Add a `validate_outputs` step.
- **Confusing the two caches.** `bench_emulation.runtime.cache` is the lab trace cache. It is not the `RuntimeEngine` LLM response cache.
- **A reduced copy of a dataset for smoke runs.** Use `dataset.limit`.
- **Expecting `out:` to be enforced.** It documents the artifact a step writes and nothing more.
- **Setting both `path` and `name` on `dataset`.** `path` wins and `name` is ignored.
- **`environment.fixtures` on a dataset item.** Fixtures stay here or on the scenario. Put per-case triggers on the item.
- **`environment` under `inputs`.** It is a sibling of `inputs`, the same key as on this file.

---

## See also

- [lab.md](lab.md)
- [scenario-generator.md](scenario-generator.md), [scheduler.md](scheduler.md), [executor.md](executor.md)
- [dataset.md](dataset.md), [environment.md](environment.md), [overlay.md](overlay.md), [flavour.md](flavour.md)
- [pipeline.md](pipeline.md)
- [Tutorial 15](../tutorials/15-world-events-and-fixtures/README.md): experiment fixtures, per-item triggers
- [summarization.md](summarization.md): judge model and conversation summarization
- [runtime-engine.md](runtime-engine.md)
- [Tutorial 03](../tutorials/03-experiments-and-analysis/README.md)
