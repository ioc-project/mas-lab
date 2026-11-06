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
| --------- | --------- |
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
| ------ | --------- | ------------- |
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
| `--override PATH=VALUE` | — | Schema-validated experiment override, applied last (repeatable) |
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
| --------- | --------- |
| `mas-lab benchmark list` | List benchmark runs |
| `mas-lab benchmark show [ID\|last]` | Run details, plots, traces |
| `mas-lab benchmark follow YAML` | Progress while a run is active |
| `mas-lab benchmark analyze ID` | Regenerate plots from existing output |
| `mas-lab benchmark clean …` | Remove run data |
| `mas-lab benchmark export ID -o RUN.tar.gz` | Pack a run into a portable archive |
| `mas-lab benchmark import RUN.tar.gz` | Restore a portable archive locally |
| `mas-lab benchmark publish-hf RUN.tar.gz --repo-id ORG/REPO` | Upload an archive tarball to a Hugging Face dataset repo |
| `mas-lab benchmark import-hf PATH_IN_REPO --repo-id ORG/REPO` | Download an archive tarball from a Hugging Face dataset repo and restore it |
| `mas-lab benchmark pipeline run PIPELINE.yaml -o OUT` | Standalone pipeline on existing output |
| `mas-lab benchmark pipeline plan PIPELINE.yaml` | Dry-run pipeline DAG |

See [components/bench/README.md](../components/bench/README.md) for the full command list.

---

## Portable benchmark archives

### `mas-lab benchmark export ID`

```bash
mas-lab benchmark export ID [OPTIONS]
```

Creates a `.tar.gz` containing:

- the benchmark output directory (`metadata.yaml`, `results.csv`, per-run artifacts)
- referenced trace-cache entries
- a minimal `source/` bundle with local experiment inputs (experiment YAMLs, referenced dataset YAMLs, referenced app tree), collected through an allowlist of folders and file types
- `MANIFEST.json` describing the archive contents, with archive-relative paths only

Export fails, and writes nothing, if a sensitive file (`.env*`, SSH or
certificate keys) ends up in the source bundle. See
[sharing-benchmarks.md](sharing-benchmarks.md) for the allowlist.

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `-o`, `--output PATH` | `./<short_id>.tar.gz` | Output archive path |
| `--no-trace-cache` | off | Skip referenced trace-cache entries |
| `--output-dir PATH` | auto | Benchmark runs root to read from |
| `--trace-cache PATH` | env / default | Trace cache root to read from |

### `mas-lab benchmark import TARBALL`

```bash
mas-lab benchmark import RUN.tar.gz [OPTIONS]
```

Restores the benchmark output locally and patches `metadata.yaml` so the
imported run points at the restored paths. When the archive contains a bundled
 experiment source tree, it is restored under `<output_dir>/_repro/source` and
`experiment_yaml_path` is redirected there.

The archive is untrusted input: members with absolute paths, `..`, or links
resolving outside the extraction root are skipped and logged.

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `--output-dir PATH` | auto | Directory to restore the benchmark output into |
| `--trace-cache PATH` | env / default | Directory to restore trace-cache entries into |
| `--dry-run` | off | Preview archive contents without writing outputs |

### `mas-lab benchmark publish-hf TARBALL`

```bash
mas-lab benchmark publish-hf RUN.tar.gz --repo-id ORG/REPO [OPTIONS]
```

Uploads an existing benchmark archive tarball to a Hugging Face **dataset**
repository. Pass `--token` or `--token-file`, or set `HF_TOKEN`. The repo is
created if missing. By default it is private and the upload opens a review PR
rather than writing to the default branch. The command prints the commit SHA.

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `--repo-id TEXT` | required | Hugging Face dataset repo id (`user-or-org/repo`) |
| `--path-in-repo TEXT` | derived from manifest | Override remote path inside the repo |
| `--visibility [private\|public]` | `private` | Visibility of the repo if it is created |
| `--revision TEXT` | `main` | Branch or commit SHA to target |
| `--create-pr / --no-create-pr` | `--create-pr` | Open a review PR instead of committing directly |
| `--token TEXT` | unset | Hugging Face access token |
| `--token-file PATH` | unset | Read the access token from a file |

Default remote path format:

```text
benchmarks/<experiment>/<short_id>/bundle.tar.gz
```

### `mas-lab benchmark import-hf PATH_IN_REPO`

```bash
mas-lab benchmark import-hf PATH_IN_REPO --repo-id ORG/REPO [OPTIONS]
```

Downloads a benchmark archive tarball from a Hugging Face **dataset** repo,
then forwards it into the regular `benchmark import` restore flow.
Pass `--token` or `--token-file`, or set `HF_TOKEN`. `--dry-run` still
uses `HF_TOKEN` when it is set, and does not require a token when it is not.

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `--repo-id TEXT` | required | Hugging Face dataset repo id (`user-or-org/repo`) |
| `--revision TEXT` | default branch | Branch or commit SHA to download from |
| `--sha256 TEXT` | unset | Expected SHA-256 of the tarball. A mismatch aborts the import. Not checked when omitted |
| `--output-dir PATH` | auto | Directory to restore the benchmark output into |
| `--trace-cache PATH` | env / default | Directory to restore trace-cache entries into |
| `--dry-run` | off | Download archive metadata and preview the local import |
| `--token TEXT` | unset | Hugging Face access token |
| `--token-file PATH` | unset | Read the access token from a file |

### Share and reproduce

```bash
# author
mas-lab benchmark export last -o run.tar.gz
shasum -a 256 run.tar.gz                      # keep this digest
mas-lab benchmark publish-hf run.tar.gz --repo-id ORG/REPO   # prints the commit SHA

# reader
mas-lab benchmark import-hf benchmarks/<experiment>/<short_id>/bundle.tar.gz \
  --repo-id ORG/REPO --revision <commit-sha> --sha256 <digest>
```

Details and trust model: [sharing-benchmarks.md](sharing-benchmarks.md).
