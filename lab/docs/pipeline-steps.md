<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline step types

Built-in **pipeline step** types for level hooks in `experiment.yaml`.
Labs add more under `lib/steps/`.

How **pipelines** work: [pipeline.md](pipeline.md). **Benchmark** command:
[benchmark.md](benchmark.md). Full OSS catalog: see the [Directory](#directory)
section below, or query it live with the commands there.

## Directory

```bash
mas-lab benchmark pipeline show path/to/experiment.yaml
curl -s http://localhost:8090/api/pipeline-step-types   # when controller running
```

## extract/ — trace → tables

Output paths are always relative to the level folder (`output:`, `subdir:`, `filename:`);
absolute paths and `..` are rejected. See [Output locations](../../docs/manifests/pipeline.md#output-locations).

| Type | Output |
|------|--------|
| `extract_trace_stats` | Per-run governance/LLM/tool counts plus token/cached usage → CSV; publishes `kind: metric` |
| `extract_mealy_stats` | Per-agent timing → CSV |
| `extract_trajectories` | State sequences |
| `extract_sys_stats` | `sys_stats` events |

`extract_trace_stats` folds the same counters from channel batches (live or
sealed) and from `events.jsonl`, and publishes ``kind: metric`` records.
`eval_mce` publishes flattened ``mce.<id>`` scores (including when it skips
because ``metrics.json`` already exists) and uses the ``judge`` resource class.

## eval/ — scoring & aggregation

| Type | Output |
|------|--------|
| `eval_mce` | One run's `events.jsonl` → `metrics.json` (`scope: run`). Judge: `experiment.evaluation.model`, else `experiment.models.judge`, else `experiment.model` / `models.main`, else application `spec.models[]`. |
| `eval_trip_planner_gt` | Trip-planner ground truth |
| `eval_adversarial` | Adversarial probes |
| `annotate_metrics` | Attach scores to run metadata |
| `collect_metrics` | Aggregate run metrics (whole-tree walk; legacy alternative to `metrics_to_dataframe` + `gather_level`) |
| `metrics_to_dataframe` | One run's `metrics.json` → tidy `data.csv` in that run folder (`scope: run`) |
| `compute_ci` | Confidence intervals |
| `validate_outputs` | Check `experiment.output_schema`'s `required_files`/`required_columns` against the output directory |

## viz/ — figures

| Type | Output |
|------|--------|
| `plotnine` | Declarative ggplot-style figures |
| `plot` / `ggplot` | Legacy plot steps |
| `plot_trajectory` | Trajectory diagrams |
| `plot_communication_flow` | Agent communication graph |
| `metrics_comparison_plot` | Scenario comparison |
| `ci_plot` | Confidence interval plots |

## data/ — plumbing

| Type | Role |
|------|------|
| `dataset` | Load scenario inputs |
| `experiment` | Run trials (nested pipelines) |
| `collect_dataframe` | Merge step outputs |
| `gather_level` | Concatenate the level below's named artifact (`config["artifact_paths"]`, populated from `in:`) into this level's `data.csv` |
| `join_dataframe` | Join tables |
| `processor` | Custom dataframe transforms |

## services/

| Type | Role |
|------|------|
| `service_start` / `service_stop` | Infra lifecycle |
| `export_otel` | OTel export |
| `file_tail` | Watch a file glob and publish `custom.{owner}.raw` on change |
| `shell` | Inline `/bin/sh -c` command (lab cwd) |

## Internal extensions (mas-lab-internal)

When `mas-lab-bench-steps` is installed: `embed_states`, `list_clickhouse_sessions`.

## Lab-local steps

Paper labs register matplotlib figure steps, e.g.:

- `lib.steps.figure_call_counts:FigureCallCountsStep` (lifecycle-control.lab)

Pattern: subclass `PipelineStep`, `register_step_type`, declare in `experiment.yaml`.

See **[custom-pipeline-steps.md](custom-pipeline-steps.md)** for the full authoring
guide — including the `per_run: true` pattern, artifact resolution APIs, and the
`lib/steps/register_steps.py` discovery convention.

## Related

- [pipeline.md](pipeline.md)
- [custom-pipeline-steps.md](custom-pipeline-steps.md)
- [benchmark.md](benchmark.md)
- [summarization.md](../../docs/manifests/summarization.md) — `eval_mce` judge model
