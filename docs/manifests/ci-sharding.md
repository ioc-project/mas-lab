<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Run an experiment's runs in CI

Generation, scheduling, execution, retry and analysis meet only in two files:
a **run plan** (which runs to run) and a **results tree** (what ran). Because
of that, each can run in its own process, and the runs themselves on as many
runners as you have.

| Step | Command | Reads | Writes |
|------|---------|-------|--------|
| Generate + schedule | `mas-lab benchmark runs plan EXP` | experiment | `plan.json`, `shard-N.json`, `matrix.json` |
| Execute (one job per shard) | `mas-lab benchmark run EXP --run-plan shard-N.json -o out-N` | a shard | a results tree |
| Merge | `mas-lab benchmark runs merge out-* -o merged` | trees | one tree |
| Retry | `mas-lab benchmark runs retry EXP --plan plan.json --results merged` | plan, tree | next plan, shards, matrix |
| Analyze | `mas-lab benchmark runs summary EXP --plan plan.json --results merged` | plan, tree | `analysis/summary.json` |

`--shard-by run|scenario|item|chunk` sets what one job runs; the matrix never
holds more than `--max-shards` (default 256, the GitHub Actions limit) jobs, and
runs beyond that are dealt round-robin into them.

## GitHub Actions

`.github/workflows/lab-experiment.yml` is a reusable workflow doing exactly
those steps: a `plan` job, a `run` matrix job (one runner per shard,
`max_parallel` at once), and a `collect` job that merges, analyzes and, when
runs are incomplete and the retrier allows it, dispatches the next round
(`max_rounds`, default 2).

```yaml
permissions:
  contents: read
  actions: write   # the collect job dispatches the next retry round

jobs:
  study:
    uses: ./.github/workflows/lab-experiment.yml
    with:
      experiment: labs/factorial-halving.lab/experiment.yaml
      shard_by: scenario
      max_parallel: 20
    secrets:
      llm_api_key: ${{ secrets.LLM_API_KEY }}
```

GitHub's own "Re-run failed jobs" also works: a shard job re-runs on its own
and `collect` never lets a failed folder replace one that finished `ok`.
Automatic rounds (`retry`) add what GitHub cannot: a retry budget across
rounds (`retry.params.max_attempts`), `Retry-After` backoff, no retry of auth or
TLS errors, and one summary of what is still incomplete.

The model endpoint and its key belong to the caller: pass them through the
experiment's infra and the `llm_api_key` secret.

## Limits

- Every round plans the experiment again from the branch tip it runs on. If the experiment
  or dataset changed between rounds, `retry` and `summary` refuse the plan naming the first
  run the experiment no longer generates, instead of mixing two studies.
- A batch started from a batch (a pipeline step calling `schedule_experiment` inside a batch)
  is refused beyond three levels deep; each batch writes a `.mas-batch-depth` marker in its
  output folder for that count.

- A plan is made from the experiment as loaded. A `pre` step that generates the
  dataset while the batch runs cannot be planned ahead: `run --run-plan` then
  refuses the plan, naming the first run the experiment does not generate.
- Run folders carry their own `result.json`, and the merge follows trace-cache
  links, so the merged tree stands alone. A shard does not share the trace
  cache with others; an experiment relying on one needs `trace_cache_portable`
  or a cache restored in each job.
- Runs that depend on each other (`resume_from`, what-if checkpoints) must be
  in one shard (`--shard-by scenario` or `chunk`), because a checkpoint is a
  file the producer writes on its own runner.
