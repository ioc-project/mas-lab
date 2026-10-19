<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI reference (benchmark)

Command-line flags for **`mas-lab benchmark run`**. Full benchmark workflow:
[benchmark.md](benchmark.md). Pipeline concepts: [pipeline.md](pipeline.md).

## `mas-lab benchmark run EXPERIMENT_YAML`

```bash
mas-lab benchmark run experiment.yaml [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--force` | off | Start fresh; ignore completed prior runs |
| `--benchmark-id ID` | — | Resume a specific benchmark by ID |
| `--progress` / `--no-progress` | progress | Real-time progress bar |
| `--dry-run` | off | Validate config and print plan only |
| `--max-runs N` | from YAML | Override `run.n_runs` |
| `--limit-scenarios N` | — | First N scenarios only |
| `--scenario-id ID` | — | Run only the scenario with this exact ID |
| `--dataset-item ID` | — | Run only the dataset item with this exact ID |
| `--sample-scenarios N` | — | Random sample of N scenarios |
| `--single-run` | off | One scenario, one run (CI shortcut) |
| `-o`, `--output-dir PATH` | auto | Experiment output directory |
| `--trace-cache PATH` | env / default | Trace cache directory |
| `--data-cache PATH` | — | Pipeline step cache directory |
| `--force-lock` | off | Break an existing run lock |
| `--flavour NAME` | experiment default | Runtime flavour YAML |
| `--infra NAME` | experiment default | Infra bundle for service steps |
| `--strategy coverage\|depth` | YAML / coverage | Run ordering strategy |
| `--set STEP.KEY=VALUE` | — | Override pipeline step config (repeatable) |
| `--clean-stale` | off | Remove outputs for dropped scenarios |
| `-b`, `--background` | off | Submit via controller daemon |

`--scenario-id` and `--dataset-item` may be used independently or together. They
are applied before `--single-run` and scenario limits, and an unknown ID fails
the run instead of silently executing a different selection.

### `--set` (step config overrides)

```bash
mas-lab benchmark run exp.yaml --set export_otel.destination=mock
```

Format: `STEP_TYPE.config_key=value`. Coercion: `true`/`false` → bool, integers → int.

---

## Other benchmark commands

| Command | Purpose |
|---------|---------|
| `mas-lab benchmark list` | List benchmark runs |
| `mas-lab benchmark show [ID\|last]` | Run details, plots, traces |
| `mas-lab benchmark follow YAML` | Progress while a run is active |
| `mas-lab benchmark analyze ID` | Regenerate plots from existing output |
| `mas-lab benchmark clean …` | Remove run data |
| `mas-lab benchmark pipeline run PIPELINE.yaml -o OUT` | Standalone pipeline on existing output |
| `mas-lab benchmark pipeline plan PIPELINE.yaml` | Dry-run pipeline DAG |

See [components/bench/README.md](../components/bench/README.md) for the full command list.

CLI commands that wrap library pipelines (`telemetry push`, `kg dump`, …):
[cli-pipelines.md](cli-pipelines.md). Man-page index:
[docs/cli/mas-lab.md](../../docs/cli/mas-lab.md).
