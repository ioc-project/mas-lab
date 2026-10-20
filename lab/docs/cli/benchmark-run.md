<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-run(1)

## NAME

`mas-lab benchmark run` — Execute an experiment YAML

## SYNOPSIS

```text
mas-lab benchmark run [OPTIONS]
```

## DESCRIPTION

Execute an experiment YAML.

## OPTIONS

| Flag | Meaning |
|------|------|
| `EXPERIMENT_YAML` | Positional argument. |
| `--force` | Force a new run even if a previous run exists (Makefile --force semantics). |
| `--override` | Schema-validated experiment override (repeatable). Applied to experiment.yaml before -x overlays and --pipeline attachments, so an overlay can supersede it. |
| `--resume` | (Deprecated) Resume an interrupted run — default behaviour now. |
| `--benchmark-id` | Specific benchmark ID to resume. |
| `--progress`, `--no-progress` | Show real-time progress bar. |
| `--dry-run` | Validate config and show what would be executed without running. |
| `--max-runs` | Override n_runs from YAML. |
| `--limit-scenarios` | Limit to first N scenarios. |
| `--scenario-id` | Run only the scenario with this ID. |
| `--dataset-item` | Run only the dataset item with this ID. |
| `--sample-scenarios` | Randomly sample N scenarios. |
| `--single-run` | Run exactly 1 scenario for quick pipeline testing. |
| `-o`, `--output-dir` | Output directory for benchmark results. |
| `--trace-cache` | Override trace-cache directory (CLI > YAML > MAS_TRACE_CACHE > $XDG_CACHE_HOME/mas/traces). |
| `--data-cache` | Override step-cache directory (CLI > MAS_DATA_CACHE > <output_dir>/.cache). Use a shared path to reuse computed pipeline outputs after benchmark import. |
| `--force-lock` | Force break existing lock. |
| `--flavour` | Runtime flavour name resolved to <experiment_dir>/flavours/<name>.yaml. Defaults to experiment's default_flavour, then 'local'. |
| `--infra` | Infra bundle name resolved to <experiment_dir>/infra/<name>.yaml. Selects which infrastructure services (OTel collector, storage, …) are provisioned by service_start / service_stop pipeline steps. Defaults to experiment's default_infra (opt-in, no default). |
| `--strategy` | Execution ordering (overrides YAML schedule.ordering). coverage (default): breadth-first — every condition gets one run before any gets a second run, maximising coverage after partial execution. depth: depth-first — complete all runs for each condition before moving on. |
| `--set` | Override a pipeline step config key from the CLI. Format: STEP_TYPE.key=value (e.g. --set export_otel.destination=mock). Value is auto-coerced: 'true'/'false' → bool, integers → int. Can be repeated for multiple overrides. |
| `--pipeline` | Attach a pipeline YAML after experiment hooks (same order as repeated flags). Format: run:post:kg:pipelines/native-to-kg.yaml or run:kg:pipelines/native-to-kg.yaml (phase defaults to post). Levels: run, item, scenario, experiment (deprecated aliases: test = item, application = experiment). |
| `-x`, `--experiment-overlay` | Apply an experiment overlay before running. Overlays deep-merge into experiment.yaml (RFC 7386 merge patch): mappings merge, but lists (run.post, experiment.post, …) are REPLACED, so an overlay that sets a hook list must carry the full desired list. Multiple -x flags are applied in order. Paths are resolved relative to experiment.yaml's directory. |
| `-b`, `--background` | Submit via controller daemon and return immediately (print worker id). |
| `--clean-stale` | Remove benchmark output for scenarios no longer in the experiment YAML (and unreferenced trace-cache entries when enabled in config). |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab benchmark migrate`
- `mas-lab --help`
