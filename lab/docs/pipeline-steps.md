<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline step types

This page lists the built-in **pipeline step** types you can use in `experiment.yaml` level hooks. Read it to find the step that turns a trace into a table or figure.

Libraries and labs add more steps. Labs keep theirs under `lib/steps/`.

Smallest working command: `mas-lab benchmark step-info eval_mce`.

How **pipelines** work: [pipeline.md](pipeline.md). Benchmark command: [benchmark.md](benchmark.md). The tables below list the steps shipped in this repository. The [Directory](#directory) shows how to query the live registry.

## Directory

List every step type registered in your installation (core plus installed
libraries), then show the parameters of one:

```bash
mas-lab benchmark step-info              # all registered types
mas-lab benchmark step-info eval_mce     # parameters, defaults, artifacts
mas-lab benchmark pipeline show path/to/pipeline.yaml   # steps of a pipeline YAML
curl -s http://localhost:8090/api/pipeline-step-types   # when the controller runs (mas-lab serve)
```

The tables below list the steps shipped in this repository. A library's steps appear only when that library is installed.

## Core steps (`mas-library-lab`)

### extract/ — trace → tables

Output paths are always relative to the level folder (`output:`, `subdir:`, `filename:`);
absolute paths and `..` are rejected. See [Output locations](../../docs/manifests/pipeline.md#output-locations).

| Type | Output |
|------|--------|
| `extract_trace_stats` | Per-run governance/LLM/tool counts plus token/cached usage → CSV; publishes `kind: metric` |
| `extract_mealy_stats` | Per-agent timing → CSV |
| `extract_trajectories` | State sequences |
| `extract_sys_stats` | Per-plugin timing from `sys_stats` events |

`extract_trace_stats` folds the same counters from channel batches (live or sealed) and from `events.jsonl`. It publishes `kind: metric` records.

`eval_mce` publishes flattened `mce.<id>` scores, also when it skips because `metrics.json` already exists. It uses the `judge` resource class.

### eval/ — scoring & aggregation

| Type | Output |
|------|--------|
| `eval_mce` | One run's `events.jsonl` → `metrics.json` (`scope: run`). Judge: `experiment.evaluation.model`, else `experiment.models.judge`, else `experiment.model` / `models.main`, else application `spec.models[]`. Metric ids and inline `prompt_metrics`: [Tutorial 10](../../docs/tutorials/10-evaluation-metrics/README.md) |
| `annotate_metrics` | Compute evaluation metrics on session-level trajectories |
| `collect_metrics` | Aggregate run metrics (whole-tree walk; legacy alternative to `metrics_to_dataframe` + `gather_level`) |
| `metrics_to_dataframe` | One run's `metrics.json` → tidy `data.csv` in that run folder (`scope: run`) |
| `compute_ci` | Confidence intervals over N runs |
| `compute_drift` | Semantic drift: 1 − cosine similarity of the embedded input and output |
| `validate_outputs` | Check `experiment.output_schema`'s `required_files`/`required_columns` against the output directory |

### viz/ — figures

| Type | Output |
|------|--------|
| `plotnine` | Declarative ggplot-style figures |
| `plot` / `ggplot` | Plot steps driven by a plot-library spec (`MASPlot` for `ggplot`) |
| `plot_trajectory` | Trajectory diagrams |
| `plot_multilevel_trajectory` | Session / MAS / agent / call swim lanes from a trace |
| `plot_communication_flow` | Agent communication graph |
| `plot_message_graph` | Whole-run agent message-flow swimlane from `kg.json` |
| `metrics_comparison_plot` | Scenario comparison bar chart from `metrics.json` files |
| `ci_plot` | Confidence interval plots |
| `pipeline_diagram` | Static SVG flow diagram of a pipeline |

### data/ — plumbing

| Type | Role |
|------|------|
| `dataset` | Load and filter scenario inputs |
| `generate_dataset` | Generate a dataset from the MAS agent's stated intent |
| `experiment` | Run trials (nested pipelines) |
| `analysis` | Consolidate experiment results |
| `collect_dataframe` | Merge step outputs |
| `gather_level` | Concatenate the level below's named artifact (`config["artifact_paths"]`, populated from `in:`) into this level's `data.csv` |
| `join_dataframe` | Concatenate or join tables |
| `to_dataframe` | Convert JSON artifacts into a tidy CSV |
| `processor` | Run a registered [processor](pipeline-processors.md) |
| `diff_trajectories` | Compare extracted trajectories between two output trees |
| `embed_trajectories` | Embeddings of trajectory text fields (JSONL) |
| `serialize` / `deserialize` | Write an artifact to a store (file, infra target) and read it back |

### services/

| Type | Role |
|------|------|
| `service_start` / `service_stop` | Infra lifecycle (from the experiment's `--infra` bundle) |
| `events_to_otel` | Replay native `events.jsonl` into OTel spans |
| `export_otel` | Convert a run's events to OTel spans and route them to a destination |
| `file_tail` | Watch a file glob and publish `custom.{owner}.raw` on change |
| `shell` | Inline `/bin/sh -c` command (lab cwd) |

## Steps from other libraries

| Library | Step types |
|---------|------------|
| `mas-library-telemetry` | `events_to_otel`, `verify_otel`, `compare_otel_spans`, `export_otel`, `clickhouse_dump`, `list_clickhouse_sessions` |
| `mas-library-kg` | `normalize_events`, `normalize_otel`, `validate_kg`, `verify_events`, `compare_kg`, `neo4j_push`, `neo4j_dump`, `plot_multilevel_trajectory_kg`, `embed_states` |

`mas-library-telemetry` registers `events_to_otel` and `export_otel` under the same names as the core steps. Its configuration keys differ (converter profile, export layers, realtime). Run `mas-lab benchmark step-info events_to_otel` for the keys in your installation. See the
[library-telemetry README](../../library-telemetry/README.md) and the
[library-kg README](../../library-kg/README.md).

## Lab-local steps

Paper labs register matplotlib figure steps, for example:

- `lifecycle_control_lab.steps.figure_call_counts:FigureCallCountsStep` (lifecycle-control.lab)

To add one:

1. Subclass `PipelineStep`.
2. Register it with `register_step`, or reference it as `module.path:ClassName` in `type:`.
3. Declare it in `experiment.yaml`.

The full authoring guide is [custom-pipeline-steps.md](custom-pipeline-steps.md). It covers the `per_run: true` pattern, artifact resolution APIs, and the `register_steps.py` discovery convention.

## Related

- [pipeline.md](pipeline.md)
- [custom-pipeline-steps.md](custom-pipeline-steps.md)
- [benchmark.md](benchmark.md)
- [summarization.md](../../docs/manifests/summarization.md): `eval_mce` judge model
