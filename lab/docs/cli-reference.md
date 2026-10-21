<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI reference (benchmark)

This page lists the `mas-lab` commands and the flags of `mas-lab benchmark`. Read it when you need an exact flag or default.

Smallest working command: `mas-lab benchmark run experiment.yaml --dry-run`.

Workflow: [benchmark.md](benchmark.md). Pipeline concepts: [pipeline.md](pipeline.md). For the authoritative list on your installation, run `mas-lab benchmark COMMAND --help`.

## Top-level `mas-lab` commands

| Command | Purpose |
|---------|---------|
| `mas-lab benchmark …` | Run experiments, inspect and manage results (this page) |
| `mas-lab control start\|stop\|status` | Controller daemon lifecycle (UI + background workers) |
| `mas-lab worker list\|show\|follow\|cancel` | Inspect and control background workers |
| `mas-lab serve` | Launch the controller daemon with the UI server |
| `mas-lab plot …` | Trajectory, communication-flow and multilevel diagrams |
| `mas-lab run processor\|pipeline step` | Run a processor or a pipeline step outside a full benchmark |
| `mas-lab pipe list\|run\|schema\|validate` | Stream-oriented processor pipelines |
| `mas-lab check`, `check-config`, `validate` | Validate a MAS spec or manifest files |
| `mas-lab config`, `init` | Show resolved paths and configuration; initialize configuration |
| `mas-lab telemetry …` (alias `otel`) | Native events and OTel spans (installed with `library-telemetry`; see its README) |
| `mas-lab kg …` | Knowledge-graph utilities (installed with `library-kg`) |
| `mas-lab eval` | LLM-as-judge scoring (installed with `library-eval`) |
| `mas-lab replay`, `demo-ui …` | Replay a recorded run; offline demo-ui export (installed with `demo-ui`) |

Commands marked "installed with" appear only when that package is installed.
Global option: `-v` / `-vv` / `-vvv` for increasing log verbosity.

## `mas-lab benchmark run EXPERIMENT_YAML`

```bash
mas-lab benchmark run experiment.yaml [OPTIONS]
```

Without `--dry-run`, the command submits the run to the controller daemon. The daemon starts automatically if needed. The command streams its output and exits non-zero if the run fails.

With `-b` the command returns immediately and prints the worker id. Follow it with `mas-lab worker follow ID`.

`--dry-run` without `-b` validates in-process and needs no daemon.

| Flag | Default | Description |
|------|---------|-------------|
| `--force` | off | Start fresh; ignore completed prior runs |
| `--benchmark-id ID` | — | Resume a specific benchmark by ID |
| `--progress` / `--no-progress` | progress | Real-time progress bar |
| `--dry-run` | off | Validate config and print the plan only |
| `--max-runs N` | from YAML | Override the number of runs per condition (`n_runs`) |
| `--limit-scenarios N` | — | First N scenarios only |
| `--scenario-id ID` | — | Run only the scenario with this exact ID |
| `--dataset-item ID` | — | Run only the dataset item with this exact ID |
| `--sample-scenarios N` | — | Random sample of N scenarios |
| `--single-run` | off | One scenario, one run (quick pipeline test) |
| `-o`, `--output-dir PATH` | auto | Experiment output directory |
| `--trace-cache PATH` | see below | Trace cache directory |
| `--data-cache PATH` | `<output_dir>/.cache` | Pipeline step cache directory (also `MAS_DATA_CACHE`) |
| `--force-lock` | off | Break an existing run lock |
| `--flavour NAME` | experiment's `default_flavour`, then `local` | Runtime flavour, resolved to `<experiment_dir>/flavours/NAME.yaml` |
| `--infra NAME` | experiment's `default_infra` (none otherwise) | Infra bundle, resolved to `<experiment_dir>/infra/NAME.yaml`, used by `service_start` / `service_stop` steps |
| `--strategy coverage\|depth` | YAML `schedule.ordering`, else `coverage` | `coverage`: every condition gets one run before any gets a second. `depth`: finish all runs of a condition first |
| `--set STEP_TYPE.KEY=VALUE` | — | Override one pipeline step config key (repeatable) |
| `--override ROOT:PATH=VALUE` | — | Schema-validated experiment override, applied last (repeatable) |
| `-x`, `--experiment-overlay YAML` | — | Deep-merge an [experiment overlay](design-experiment-overlays.md) into `experiment.yaml` before running (repeatable, applied in order, paths relative to `experiment.yaml`; lists are replaced, not appended) |
| `--pipeline LEVEL[:PHASE]:REF` | — | Attach a pipeline after the experiment's own hooks (repeatable) |
| `--clean-stale` | off | Remove outputs for scenarios no longer in the experiment YAML |
| `-b`, `--background` | off | Submit via the controller daemon and return immediately |

Trace cache directory precedence: `--trace-cache`, then the experiment YAML,
then `MAS_TRACE_CACHE`, then `$XDG_CACHE_HOME/mas/traces`.

Use `--scenario-id` and `--dataset-item` alone or together. They apply before `--single-run` and scenario limits. An unknown ID fails the run. It never runs a different selection silently.

### `--set` (step config overrides)

```bash
mas-lab benchmark run exp.yaml --set export_otel.destination=mock
```

Format: `STEP_TYPE.config_key=value`. `STEP_TYPE` is the step's `type:` (see [pipeline-steps.md](pipeline-steps.md)).

Values are coerced. `true` and `false` become booleans. Then integers, then floats. Anything else stays a string. A malformed entry is ignored with a warning.

### `--pipeline` (attach a pipeline)

```bash
mas-lab benchmark run exp.yaml \
  --pipeline run:post:kg:pipelines/native-to-kg.yaml
```

`LEVEL` is `run`, `item`, `scenario` or `experiment`. The older names `application` and `test` are aliases for `experiment` and `item`.

`PHASE` is `pre` or `post`. It defaults to `post`. `REF` is a path or a `library:path` reference.

---

## Other benchmark commands

| Command | Purpose |
|---------|---------|
| `mas-lab benchmark list [--status S] [--experiment YAML] [--limit N] [--format F]` | List benchmark runs (default limit 20) |
| `mas-lab benchmark show [ID\|last] [info\|plots\|events-trace\|otel-traces]` | Run details, plots, traces. With `-r` and a lab or experiment path, shows the tree (`-d exp\|scenario\|item\|run`) |
| `mas-lab benchmark artifact list\|show` | List and show artifacts of an experiment |
| `mas-lab benchmark follow YAML [--once] [--interval S]` | Progress while a run is active |
| `mas-lab benchmark analyze ID` | Regenerate statistics and plots from existing output |
| `mas-lab benchmark clean YAML [--scenario ID] [--dry-run] [--keep-traces]` | Delete cached runs so they are re-executed |
| `mas-lab benchmark update ID`, `rename ID SLUG`, `migrate SRC [DST]` | Edit metadata, rename or move a run directory |
| `mas-lab benchmark export ID` / `import TARBALL` | Pack and restore a benchmark archive |
| `mas-lab benchmark step list\|show\|restart` | Inspect or restart a pipeline step of a run |
| `mas-lab benchmark step-info STEP_TYPE` | Parameters, defaults and artifacts of a pipeline step type |
| `mas-lab benchmark pipeline run PIPELINE.yaml [-o OUT]` | Run a standalone pipeline (`--only`, `--force`, `--var K=V`, `--dry-run`, `--no-parallel`) |
| `mas-lab benchmark pipeline plan\|show\|validate PIPELINE.yaml` | Dry-run the DAG, display it, or validate it |
| `mas-lab benchmark daemon start\|stop\|status` | Manage the UI server as a background daemon |

See [components/bench/README.md](../components/bench/README.md) for the full command list.

CLI commands that wrap library pipelines (`telemetry push`, `kg dump`, …):
[cli-pipelines.md](cli-pipelines.md). Man-page index:
[docs/cli/mas-lab.md](../../docs/cli/mas-lab.md).
