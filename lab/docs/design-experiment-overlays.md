<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Experiment overlays

This page explains experiment overlays. Read it when the same experiment must run differently in CI, local development, and full benchmarks.

An **experiment overlay** amends an `experiment.yaml` at load time without editing it. The overlay changes how the experiment runs: parallelism, timeouts, cache posture, number of runs, dataset size. The experiment file keeps describing what is measured: scenarios, dataset, design, pipeline hooks.

Smallest working command:

```bash
mas-lab benchmark run experiment.yaml -x overlays/local-dev.yaml
```

Agent and MAS overlays work the same way, on other manifests. Schema of the base file: [manifests/experiment.md](../../docs/manifests/experiment.md).

## Why

An experiment YAML is the hypothesis under test. It should stay stable across environments. Operational settings differ by context:

- A CI gate wants one run of one item.
- Local development wants the trace cache on and moderate parallelism.
- A full benchmark wants many runs.

Keeping these settings in the experiment file forces edits and noisy history on every environment change. A buried setting, such as a disabled trace cache, silently makes every `benchmark run` execute fresh LLM calls.

## Format

An overlay is a YAML file. Its `spec:` holds any keys valid inside `experiment:`. All fields are optional.

```yaml
# overlays/local-dev.yaml
apiVersion: mas/v1
kind: ExperimentOverlay
metadata:
  name: local-dev
spec:
  schedule:
    parallel_scenarios: 4
    timeout: 120
  bench_emulation:
    runtime:
      cache: content-addressed   # reuse recorded traces; never disable in local dev
  run:
    n_runs: 1
```

```yaml
# overlays/ci-fast.yaml
apiVersion: mas/v1
kind: ExperimentOverlay
metadata:
  name: ci-fast
spec:
  dataset:
    limit: 1                     # first dataset item only
  schedule:
    parallel_scenarios: 1
  run:
    n_runs: 1
```

`runtime.cache` is `content-addressed` (default), `disabled` or `forced`. `bench_emulation` is the block that configures how the benchmark emulates the runtime. See the `bench_emulation` section of [manifests/experiment.md](../../docs/manifests/experiment.md).

The loader also accepts a bare overlay (the `spec:` keys at top level) and an `experiment:` wrapper. `kind` should be `ExperimentOverlay`. Other kinds log a warning.

## Merge rules

The loader applies the overlay as a JSON Merge Patch (RFC 7386):

- A mapping merges recursively into the base.
- A scalar replaces the base value.
- A **list replaces the base list**. To add a pipeline hook, give the full list, including the base entries you keep.

## Using overlays

```bash
# local development
mas-lab benchmark run experiment.yaml -x overlays/local-dev.yaml

# CI gate: overlay plus a CLI limit
mas-lab benchmark run experiment.yaml -x overlays/ci-fast.yaml --max-runs 1

# several overlays, applied left to right
mas-lab benchmark run experiment.yaml -x overlays/local-dev.yaml -x overlays/ci-fast.yaml
```

Relative overlay paths resolve from the directory of `experiment.yaml`. `--dry-run` validates the merged result without running anything.

## Precedence (lowest to highest)

```text
experiment.yaml
  -> experiment overlays (-x, in order)
    -> --infra
      -> --flavour
        -> --max-runs
          -> --set STEP_TYPE.KEY=VALUE
```

CLI flags always override an overlay. Flag reference:
[cli-reference.md](cli-reference.md).

## Related

- Agent and MAS overlays: [manifests/overlay.md](../../docs/manifests/overlay.md)
- `--flavour` selects the runtime posture. `--infra` selects the infrastructure bundle (collector, storage) for service steps.
- [designing-experiments.md](designing-experiments.md): one canonical experiment file plus CLI flags for smoke runs.
