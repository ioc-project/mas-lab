<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Benchmark run state and locking

This page explains how `mas-lab benchmark run` tracks progress, skips finished work, and stays safe under concurrent invocations. Read it when a re-run does not behave as you expect.

Prerequisite: [benchmark.md](benchmark.md) (execution and pipeline phases).

Smallest working command: re-run `mas-lab benchmark run experiment.yaml --progress`. Finished work is skipped.

## Phases

```text
validate YAML → schedule (scenario × item × run) → execute runs → post pipeline
```

| Phase | Skip when |
|-------|-----------|
| Run execution | Trace fingerprint matches cache (`run_info.json` + content hash) |
| Pipeline step | Step fingerprint unchanged (`.cache/<step>.fingerprint`) |

Re-run the same command to resume or refresh outputs:

```bash
mas-lab benchmark run experiment.yaml --progress
```

Force re-execution with one of these:

- `--force` re-executes runs.
- `benchmark pipeline run ... --force STEP` re-executes one step.
- `benchmark step restart BENCHMARK_ID STEP_ID` restarts a step of a finished benchmark.

## Trace cache

The **trace cache** stores the `events.jsonl` traces of completed runs. A later run with the same fingerprint reuses them.

The cache root resolves in this order:

1. `--trace-cache`
2. The experiment YAML
3. `MAS_TRACE_CACHE`
4. `$XDG_CACHE_HOME/mas/traces/`

`mas-lab config` prints the effective path. Run directories can hold `.run_ref` files that point into the cache instead of copying the JSONL.

Configure: [docs/user-config.md](../../docs/user-config.md).

## Advisory lock

Each experiment output directory holds a PID-based lock (`.benchmark.lock`). The lock stops two `benchmark run` processes from corrupting the same tree.

Break a stale lock (dead PID) with `--force-lock`. Do not use `--force`: it re-executes the runs.

Implementation: [`lab/components/bench/src/mas/lab/benchmark/lock.py`](../components/bench/src/mas/lab/benchmark/lock.py).

## Inspect state

```bash
mas-lab benchmark show last                     # details of the latest benchmark
mas-lab benchmark show labs/my.lab -r -d run    # tree of a lab or experiment, per-run status
mas-lab benchmark list --limit 10
```

## Related

- [Experiments and analysis](../../docs/tutorials/03-experiments-and-analysis/README.md) — caching and reproducibility
- [pipeline.md](pipeline.md) — step fingerprints and `depends_on`
