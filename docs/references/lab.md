<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab package documentation

This page lists where the `mas-lab` package keeps its own docs: the benchmark engine, pipeline steps, and experiment design. Read it when you run experiments.

| Topic | Path in repository |
|-------|-------------------|
| Lab package index | [`lab/docs/index.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/index.md) |
| Benchmarking | [`lab/docs/benchmark.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/benchmark.md) |
| Designing experiments | [`lab/docs/designing-experiments.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/designing-experiments.md) |
| Pipeline steps | [`lab/docs/pipeline-steps.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/pipeline-steps.md) |
| Labs quickstart | [`lab/docs/labs-quickstart.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/labs-quickstart.md) |

For the YAML fields of experiments, datasets, and pipelines, see [Lab](index.md#lab).

## Which application runs

The manifest an experiment names (`application.manifest`) is the manifest that runs. It runs as a MAS when its `kind` is `mas`, `app` or `workflow`, or when it is named `mas.yaml`, `mas.yml` or `mas.json`. It runs as a single agent when its `kind` is `agent`, when it is an OASF agent record, or when it has no `kind` and no `mas.yaml` or `mas.yml` beside it. A `kind` that is not recognised (a typo), or a file with no `kind` next to a `mas.yaml` / `mas.yml`, is refused with a message naming the file (and saying the sibling is not used). `mas-lab validate` prints `runs: <path>` for an experiment (and fails when the named file is refused), and `mas-lab run --dry-run` prints the same path under `Manifest`.

## Dataset items and scenarios

An item may list the scenarios it applies to (`scenarios`). An id the experiment does not declare, or an empty list, is an error at load time, not a silent skip. The dry run prints how many items the lists leave out (`n items skipped by scenario`), including couplings that name an item in a scenario it does not list.

## Plugin import before runs

Runs execute on worker threads, and plugin packages import lazily and import their siblings, so two threads that first touch different modules of one package can deadlock on the per-module import locks (`deadlock detected by _ModuleLock`). The batch therefore imports, once and on one thread before any run starts (about two to three seconds), every installed plugin module (`mas.runtime.registry.preload`) and the `mas.runtime` and `mas.ctl` packages (`mas.ctl.preload`); the controller does it at startup. A spawn worker (`executor.worker_processes`) is not preloaded: it runs one job at a time, so no two runs race there, and a full preload would add about 250 MB and a few seconds to every worker process. Telemetry and KG plugins list the SDK and handler modules they import lazily under `warm:` as well. Every plugin is imported, not only the ones a manifest names, because a run also loads plugins the manifest never mentions (a layer composed in, a tool provider chosen by protocol). A plugin that keeps an SDK out of import time (the A2A plugin and `grpc`) lists those modules under `warm:` in its `library.yaml`; they are imported too when installed. Later imports are no-ops. A plugin whose requirements are missing is skipped at that point and fails with its own message when a run uses it. The `library_ioa.plugins.mcp` package `__init__` imports nothing; import from its modules (`.provider`, `.layer`, `.client`). A plugin added by an experiment-local library after the batch starts is not preloaded. A single `mas-ctl run` session is not preloaded.

## A failed or interrupted batch

When a batch raises or is interrupted (`Ctrl+C`), the services it started are stopped (including when writing the results file fails, or when stopping a service itself is interrupted), the batch is marked failed in `metadata.yaml`, and the original exception is re-raised. The `post` steps do not run: they analyse a complete batch, and over partial output they would spend time and model calls on results that look finished. The results file is not written on an abort: the runs that finished are in their run folders. A service that cannot be stopped marks an otherwise complete batch failed. A failure in an experiment-level `pre` step happens before any service starts.

## See also

- [Tutorial 3: experiments and analysis](../tutorials/03-experiments-and-analysis/README.md)
- [Experiment manifest](../manifests/experiment.md)
- [Runtime package documentation](runtime.md)
