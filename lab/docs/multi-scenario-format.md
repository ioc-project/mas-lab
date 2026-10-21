<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Multi-scenario experiment format

This page explains how an **experiment manifest** combines **scenarios**, a **dataset**, and **runs**. Read it when you write or edit `experiment.yaml`.

Smallest working example: the `scenarios:` block below, plus a `dataset:` path.

Terms: [glossary.md](../../docs/glossary.md). Schema:
[manifests/experiment.md](../../docs/manifests/experiment.md).

## Execution grid

```text
for scenario in scenarios:
  for item in dataset:
    for run_index in 1..n_runs:
      execute (scenario overlays × item)
```

On disk, the directory tree is the artifact tree. Each level (experiment, scenario, item, run) owns the artifacts it declares in its own `artifacts:` block:

```text
<output_dir>/
  data.csv                              # experiment-level artifact (e.g. gathered df)
  <scenario-id>/
    data.csv                            # scenario-level artifact
    item<N>/
      data.csv                          # item-level artifact
      r<R>/
        traces/events.jsonl             # run-level artifact (trace)
        metrics.json                    # run-level artifact (eval_mce output)
        data.csv                        # run-level artifact (metrics_to_dataframe output)
```

A `gather_level` step at `item:`, `scenario:` or experiment `post:` collects the named artifact of the level below and passes it upward (see [pipeline-steps.md](pipeline-steps.md)
and the `in:`/`out:`/`scope:` fields in
[manifests/pipeline.md](../../docs/manifests/pipeline.md)).

## `scenarios:`

```yaml
experiment:
  scenarios:
    - id: linear
      overlays:
        logic: [linear]
        control: []
        infra: []
      tags: [topology]
    - id: moderator
      overlays:
        logic: [moderator-broker]
        control: []
        infra: []
```

| Field | Meaning |
|-------|---------|
| `id` | Folder name and table key |
| `overlays` | Layered stacks (`logic`, `control`, `infra`) from `configs_dir` |
| `tags` | Free-form labels for grouping. Stored with the scenario; not a CLI filter. To select scenarios, use `--scenario-id` or `--limit-scenarios` |

## `dataset:`

```yaml
  dataset:
    path: ./datasets/queries.yaml
```

Format: [dataset.md](../../docs/manifests/dataset.md).

## MAS binding

The `application:` key names the MAS or agent under test:

```yaml
  application:
    app: trip-planner
    configs_dir: ./overlays
```

Or point at an explicit manifest:

```yaml
  application:
    manifest: ./mas.yaml
    configs_dir: ./overlays
```

`applications:` (list) is deprecated; the loader keeps the first item and warns.

## Post-run pipeline

Hooks use the level names `run:`, `item:`, `scenario:` and experiment `post:`. Run-scoped steps write one artifact. At higher levels, `gather_level` concatenates one child layer only.

```yaml
  run:
    n_runs: 1
    artifacts:
      trace: { type: trace, path: "{run_dir}/traces/events.jsonl" }
      metrics: metrics
      df: dataframe
    post:
      - name: eval-quality
        type: eval_mce
        in: trace
        out: metrics
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
    - {name: gather-experiment, type: gather_level, in: df, out: df, depends_on: [gather-scenario]}
```

Step types: [pipeline-steps.md](pipeline-steps.md).

## Example

[design-space.lab/02-topologies/experiment.yaml](../../labs/design-space.lab/02-topologies/experiment.yaml).
