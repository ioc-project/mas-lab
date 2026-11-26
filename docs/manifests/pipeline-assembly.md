<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Assemble an experiment from six blocks

Generator, scheduler, executor, retrier, analyzer, and pipeline are six blocks. Each exposes
a small API so a full experiment can be assembled from parts, including
visually in the UI. This page is the pipeline block and the lab API a
step may call. There is no UI code in this tree; a UI reads
`mas.lab.benchmark.pipeline.assembly.catalog()`.

## Lab API

A step may schedule a batch, read its status and results, stop it, or ask
for the next runs. The same calls are on the controller (`schedule_batch`,
`get_batch`, `stop_batch`, `next_runs`), authenticated like any other
control call.

| Call | Effect |
| --- | --- |
| `schedule_batch(experiment_yaml, plan_file=...)` | Start a new experiment batch (only the runs of a plan file when given). Runs in the controller when one is up, otherwise as a child `mas-lab benchmark run`. |
| `batch_status(batch_id)` | Status, results, incomplete runs |
| `stop_batch(batch_id)` | Cancel the worker |
| `next_runs(...)` | Remaining error runs, successive halving, or a score-driven cut. Halving and the score cut need a score for every scenario and rank every scenario of the batch, not only the unfinished ones. |

One pipeline run may start at most `max_scheduled_batches` batches (default 8), counted in one budget every step of it shares. A runaway loop stops there.

## Steps

| Step | Role |
| --- | --- |
| `schedule_experiment` | Call `schedule_batch`. Uses `config.experiment`, or an upstream `experiment_yaml`. |
| `await_batch` | Wait until that batch is terminal |
| `next_runs` | Compute the next runs from scores and leftover errors. `experiment` plans an experiment when there is no batch; `plan_path` writes the chosen runs as a run-plan file. |
| `propose_prompts` | LLM-as-judge: next user prompts from scores and complete traces (`eval_mce` scope wrappers: `unit`, `evidence`). Offline: `prompts_path`. |
| `inject_experiment` | Write a follow-up experiment whose dataset is those prompts, with every `./` path made absolute. With upstream runs it also writes `runs.json`, a plan restricted to their scenarios (the design stays whole). |

```yaml
pipeline:
  name: prompt-loop
  max_scheduled_batches: 3
  steps:
    - name: round-1
      type: schedule_experiment
      config: {experiment: ./round.yaml}
    - name: wait-1
      type: await_batch
      depends_on: [round-1]
    - name: propose
      type: propose_prompts
      depends_on: [wait-1]
      config:
        n: 1
        unit: mas
        evidence: io
    - name: inject
      type: inject_experiment
      depends_on: [propose]
      config: {experiment: ./round.yaml}
    - name: round-2
      type: schedule_experiment
      depends_on: [inject]
```

The inner round's `run.post` names the modes: `extract_trace_stats` is
`live` (events as they come, possibly incomplete) and `eval_mce` is
`batch` (complete runs only). `propose_prompts` is batch: it reads MCE
scores and complete traces only. Incomplete runs never reach evaluation.
A live stage feeds a batch stage only through that gate.

`next_runs` strategies:

- `remaining` — runs whose `result.json` is missing or not `ok`
- `successive_halving` — keep the strongest scenarios by score
- `score_driven` — keep scenarios at or above the median score

A prompt sweep is this loop: run, score with `eval_mce`, propose a new
user prompt, inject it as the next experiment design, schedule that
batch. That used to need an outside program calling MAS Lab again and
again.

## Stage graph

See [completeness](completeness.md) for `batch` / `live` / `mixed`.
`catalog()` lists every registered step with its default mode, config
params, and typed ports, and `allowed_edges`: only a batch stage waits for a
complete run, and a live stage feeds it only through the completeness gate.

Companion lab in this tree: `labs/factorial-halving.lab`.
