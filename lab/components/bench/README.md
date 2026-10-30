<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-bench

Runs reproducible benchmark experiments: scenarios x dataset items x runs, then pipeline steps on the resulting `events.jsonl` logs. Runs resume from the trace cache when inputs match.

Use it when you compare multi-agent system (MAS) variants, for example an ablation, and need statistics and plots. Terms are in the [glossary](../../../docs/glossary.md). The user guide is [lab/docs/benchmark.md](../../docs/benchmark.md).

## Quick start

```bash
uv pip install -e "mas-lab[all]"            # all extras
uv pip install -e mas-lab/components/bench  # or this component alone

mas-lab benchmark run my-experiment.yaml --dry-run    # validate config, show plan
mas-lab benchmark run my-experiment.yaml --progress   # execute (resumes a prior run)
mas-lab benchmark follow my-experiment.yaml           # watch progress from a second terminal
mas-lab benchmark show last                           # inspect results
```

## Experiment YAML

```yaml
experiment:
  name: my-trip-planner-ablation
  default_flavour: local
  application:
    manifest: path/to/mas.yaml
    configs_dir: ./overlays
  scenarios:
    - id: baseline
      overlays: {logic: [], control: [], infra: []}
    - id: with-mitigation
      overlays: {logic: [], control: [full_mitigation], infra: []}
  dataset:
    path: ./datasets/queries.yaml
    limit: 20
  run:
    n_runs: 5
    artifacts:
      trace: trace
      df: dataframe
    post:
      - {name: extract-trace-stats, type: extract_trace_stats, in: trace, out: df}
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
  execution:
    strategy: coverage
```

## Commands

### `benchmark run`

Executes an experiment. A run with `run.n_runs: 5` resumes from where it stopped. Results go under the lab output root derived from `experiment.name` (override with `-o`).

```bash
mas-lab benchmark run experiment.yaml
mas-lab benchmark run experiment.yaml --force          # always start fresh
mas-lab benchmark run experiment.yaml --dry-run        # validate + plan only
mas-lab benchmark run experiment.yaml --single-run     # 1 scenario, handy for CI
mas-lab benchmark run experiment.yaml --limit-scenarios 10
mas-lab benchmark run experiment.yaml --sample-scenarios 20 --seed 42
mas-lab benchmark run experiment.yaml --max-runs 3
mas-lab benchmark run experiment.yaml --strategy depth # depth-first (default: coverage)
mas-lab benchmark run experiment.yaml -o /tmp/results
```

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `--force` | off | Always create a new run |
| `--benchmark-id` | - | Resume a specific benchmark by ID |
| `--progress / --no-progress` | progress | Real-time progress bar |
| `--dry-run` | off | Validate config + show plan, no execution |
| `--max-runs` | from YAML `run.n_runs` | Override `n_runs` |
| `--limit-scenarios` | - | Limit to first N scenarios |
| `--sample-scenarios` | - | Randomly sample N scenarios |
| `--single-run` | off | Run exactly 1 scenario |
| `-o / --output-dir` | derived from `experiment.name` | Output directory |
| `--trace-cache` | `$XDG_CACHE_HOME/mas/traces` | Override trace-cache directory |
| `--force-lock` | off | Break existing lock |
| `--flavour` | `local` | Runtime flavour YAML name |
| `--strategy` | `coverage` | `coverage` (breadth-first) or `depth` (depth-first) |

### Other `benchmark` commands

```bash
# list: --status running|completed|failed|partial, --limit (20), --format simple|markdown|csv|json|jsonl
mas-lab benchmark list --status completed --limit 10 --format markdown

# show: one run, or a section of it
mas-lab benchmark show <id>
mas-lab benchmark show last plots          # also: events-trace, otel-traces
mas-lab benchmark show last --format json

# follow: poll run_info.json files and stream a tally until all runs finish
mas-lab benchmark follow experiment.yaml --interval 10
mas-lab benchmark follow experiment.yaml --once        # single snapshot

# analyze: regenerate plots and statistics from existing output (no re-execution)
mas-lab benchmark analyze <id> --experiment-yaml path/to/experiment.yaml

# update: edit metadata of a run
mas-lab benchmark update <id> --name "Final ablation v2" --add-tag ablation --remove-tag wip

# step: inspect and restart individual pipeline steps of a run
mas-lab benchmark step list <id>
mas-lab benchmark step show <id> <step_id>
mas-lab benchmark step restart <id> <step_id>

# pipeline: run a standalone post-processing pipeline YAML on existing output
mas-lab benchmark pipeline validate pipeline.yaml   # schema check
mas-lab benchmark pipeline show pipeline.yaml       # step graph
mas-lab benchmark pipeline plan pipeline.yaml       # execution plan
mas-lab benchmark pipeline run pipeline.yaml
```

### Share a run

```bash
# export: pack output, referenced trace cache, and a minimal source bundle
mas-lab benchmark export last -o run.tar.gz [--no-trace-cache]

# import: restore an archive (--dry-run to preview)
mas-lab benchmark import run.tar.gz --output-dir DIR --trace-cache DIR

# publish-hf: upload to a Hugging Face dataset repo (private, review PR by default)
mas-lab benchmark publish-hf run.tar.gz --repo-id ORG/REPO

# import-hf: download and restore, pinned to a commit and a SHA-256
mas-lab benchmark import-hf PATH_IN_REPO --repo-id ORG/REPO --revision SHA --sha256 DIGEST
```

Archive layout and trust model: [sharing-benchmarks.md](../../docs/sharing-benchmarks.md).

## Post-processing pipeline

A pipeline YAML declares steps and their dependencies. Steps run in topological order; independent steps run in parallel.

```yaml
name: post-process
steps:
  - name: extract
    type: extract_trajectories

  - name: stats
    type: extract_trace_stats
    depends_on: [extract]

  - name: eval
    type: eval_mce
    scope: run              # scores ONE run per invocation; needs run-level context
    depends_on: [extract]
    config:
      metrics: [goal_success_rate]

  - name: plot
    type: plot
    depends_on: [eval]
    config:
      spec: shapley_bars
```

Step implementations are library plugins registered in the runtime registry through the `types:` / `plugins:` block of [`library-lab/library.yaml`](../../../library-lab/library.yaml). `mas-lab benchmark pipeline validate` lists the registered step types. The full catalog is in [pipeline-steps.md](../../docs/pipeline-steps.md). Steps such as `embed_states` and `list_clickhouse_sessions` can live in your own package and register through entry points.

| Category | Step types |
| ---------- | ------------ |
| `extract/` | `extract_trace_stats`, `extract_mealy_stats`, `extract_trajectories` (builds `trajectories.jsonl` from `events.jsonl`) |
| `eval/` | `eval_mce` (MCE LLM-as-judge scoring on `events.jsonl`), `collect_metrics`, `compute_ci` |
| `viz/` | `plot` (declarative figure from a `plot_library/` spec: `shapley_bars`, `ablation_heatmap`, `trajectory_gantt`), `plot_trajectory` (per-run SVG), `plot_multilevel_trajectory` (swimlane HTML/SVG), `ci_plot` |
| `data/` | `to_dataframe`, `serialize`, `processor` (run a registered trajectory processor by name) |
| `services/` | `service_start`, `export_otel` |
| other | `experiment` (run a MAS scenario via `mas-ctl`), `dataset` (load / filter a scenario dataset), `annotate_metrics` (attach scores to run metadata), `embed_trajectories` (embed trajectory strings for manifold analysis) |

Shared helpers are in `pipeline/lib/` (`data_source`, `plot_lib`, `plot_specs/`).

## Related commands

Score one trace with MCE metrics (for batches, use the `eval_mce` step; see [library-eval](../../../library-eval/README.md)):

```bash
mas-lab eval path/to/traces/events.jsonl --metric groundedness --json
```

Draw visualisations directly from a trace file:

```bash
mas-lab plot trajectory events.jsonl -o trajectory.png
mas-lab plot multilevel-trajectory events.jsonl -o multilevel.png
mas-lab plot communication-flow events.jsonl -o flow.png
mas-lab plot list output/benchmark/runs/
```

## Python API

Prefer `mas-lab benchmark run experiment.yaml`. For an in-process run:

```python
from pathlib import Path
from mas.lab.benchmark.worker import run_benchmark_sync

ok = run_benchmark_sync(
    Path("labs/design-space.lab/02-topologies/experiment.yaml"),
    flavour_name="local",
    output_dir=Path.home() / ".local/share/mas/labs/my-run",
)
```

## Package layout

```
components/bench/src/mas/lab/benchmark/
+-- dataset.py              dataset loading, filtering, sampling
+-- experiment.py           ExperimentRunner + ExperimentConfig
+-- metadata.py             BenchmarkMetadata (run_info.json)
+-- lock.py                 advisory file lock (concurrent-run safety)
+-- deduplication.py        trace-cache dedup (skip already-seen prompts)
+-- analysis.py             statistical analysis helpers
+-- migrate.py              run directory migration utilities
+-- pipeline/
    +-- pipeline.py         pipeline loader + validator
    +-- executor.py         topological executor (parallel)
    +-- registry.py         step-type registry
    +-- cache.py            step output cache
    +-- steps/              experiment.py, extract_trajectories.py, eval_mce.py,
                            plot_multilevel_trajectory.py, ...
```

## Documentation

| Document | Description |
| ---------- | ------------- |
| [benchmark.md](../../docs/benchmark.md) | Benchmark guide: quickstart, concepts, lifecycle |
| [pipeline.md](../../docs/pipeline.md) | Pipeline architecture and YAML schema |
| [pipeline-steps.md](../../docs/pipeline-steps.md) | Step type reference |
| [pipeline-processors.md](../../docs/pipeline-processors.md) | Processor registry and plot library |
| [library-eval/README.md](../../../library-eval/README.md) | MCE metrics and `mas-lab eval` |
| [designing-experiments.md](../../docs/designing-experiments.md) | Designing ablations and Shapley experiments |
| [benchmark-state-architecture.md](../../docs/benchmark-state-architecture.md) | Run state machine and locking |
| [sharing-benchmarks.md](../../docs/sharing-benchmarks.md) | Export, import, and Hugging Face publishing |
| [multi-scenario-format.md](../../docs/multi-scenario-format.md) | Multi-scenario dataset format |
