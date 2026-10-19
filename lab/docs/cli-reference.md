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
| ------ | --------- | ------------- |
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
- a minimal `source/` bundle with local experiment inputs (experiment YAMLs, referenced dataset YAMLs, referenced app tree)
- `MANIFEST.json` describing the archive contents

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
repository. Authentication uses the `HF_TOKEN` environment variable.

| Flag | Default | Description |
|------|---------|-------------|
| `--repo-id TEXT` | required | Hugging Face dataset repo id (`user-or-org/repo`) |
| `--path-in-repo TEXT` | derived from manifest | Override remote path inside the repo |

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
Authentication uses the `HF_TOKEN` environment variable.

| Flag | Default | Description |
| ------ | --------- | ------------- |
| `--repo-id TEXT` | required | Hugging Face dataset repo id (`user-or-org/repo`) |
| `--output-dir PATH` | auto | Directory to restore the benchmark output into |
| `--trace-cache PATH` | env / default | Directory to restore trace-cache entries into |
| `--dry-run` | off | Download archive metadata and preview the local import |
