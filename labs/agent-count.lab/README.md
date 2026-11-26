<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent count lab

This lab shows `count` on an agency entry and what scaling a group of identical agents does. A moderator sends one question to 1, 2, 4, 8 or 16 identical workers. The lab measures how much they agree, how often the majority is right, and what it costs. It reads `events.jsonl` only and uses no judge model. Read this page to run it.

Terms: [glossary.md](../../docs/glossary.md). The attribute: [Groups of agents with `count`](../../docs/manifests/mas.md#groups-of-agents-with-count).

## Run

Smallest working command, from the repo root. You should see a progress bar, then two figures and two CSV files under the output path below.

```bash
mas-lab benchmark run labs/agent-count.lab/experiment.yaml --progress

# Validate the configuration without calling a model
mas-lab benchmark run labs/agent-count.lab/experiment.yaml --dry-run
```

The agents say `model: any`, so the model comes from `experiment.models.main` (`gpt-4o-mini`). To use another model:

```bash
mas-lab benchmark run labs/agent-count.lab/experiment.yaml \
  --override 'experiment:experiment.models.main=gemma4'
```

The run is 5 scenarios × 10 questions × 10 runs = 500 sessions and 3,100 worker calls, about 6 minutes on gpt-4o-mini.

Three caches can make every worker return the same answer, and then agreement is 1.0 for any n. Check each one:

- **The MAS Lab LLM cache** (`llm_cache` infra step): keep it off. See [llm-cache.md](../../docs/manifests/llm-cache.md).
- **A gateway cache.** A LiteLLM gateway caches identical requests on the server, and the workers send identical requests. [experiment.yaml](experiment.yaml) lists `overrides: [.mas.agent[worker].spec.models[0].extra.cache.no-cache=true]`, which every run applies, to skip it. Delete that line for an endpoint that rejects unknown request fields.

If the endpoint refuses some calls (an error, a rate limit), the executor records those runs as not complete, re-runs them, and keeps them out of the metrics and the figures. A run that cannot complete leaves a gap in `collect_metrics` (fewer runs in `n` of `ci_by_run.csv`): check `n` before you read the intervals.
- **The trace cache.** A run with the same inputs reuses its stored trace, and a change to a worker's prompt or `extra` is not part of those inputs. After you edit the worker, pass a fresh `--trace-cache DIR` (or `--force`).

Quick check that the workers differ: `agreement` below 1.0 for some question at n = 4 or more.

## What it shows

- **Scaling:** one `count: 1` entry in [apps/crowd-qa/mas.yaml](apps/crowd-qa/mas.yaml) makes the group `worker`. Each overlay in [overlays/](overlays) is one line, `$op.count: {worker: n}`. The agent ids are `worker-1` to `worker-n` in every scenario.
- **Group communication:** the moderator's `deterministic_parallel` pattern sends the same question to every worker in one batch and returns all answers. `delegates_to: [worker]` names the group, not each worker.
- **Metrics** (`eval_metric` plugins in [metrics.py](agent_count_lab/metrics.py), scored per run by the `eval_mce` step; no judge model, no model call):

| Metric | Meaning |
| --- | --- |
| `crowd_agreement` | Share of workers that gave the most common answer. 1.0 for one worker. |
| `crowd_accuracy` | Share of workers that gave the right answer (`ground_truth` in [the dataset](datasets/crowd-questions.yaml)). |
| `crowd_majority_correct` | 1 when the right answer is the strict plurality. A tie counts as 0. |
| `crowd_agents` | Workers that gave a complete answer. Below the scenario's size means some calls were cut off or failed; their text is never counted as an answer. `details` counts `n_truncated` and `n_failed` calls. |
| `crowd_tokens` | Tokens of all model calls in the run. |

- **Repeated runs and intervals:** every cell runs `n_runs: 10` times. `collect_metrics` makes one tidy table, and `compute_ci` takes the mean over the questions per run, then a Student t 95% interval across the runs (`ci_by_run.csv`); a second `compute_ci` gives the interval per question (`ci_by_question.csv`). The figures draw those tables with `plotnine`.

Accuracy of one worker is the same quantity at every n, so its differences between n are the noise of the sample (about ±0.1 with 10 questions and a few runs; the figures show a 95% interval): read the other lines against it.

Accuracy of one worker stays flat as n grows. If the workers' mistakes are independent, `majority_correct` rises with n. If they are correlated (the same wrong answer from the same model), it does not, and agreement then measures confidence, not truth.

## Limits

- The workers do not talk to each other. The communication is one fan-out and one collection.
- Every worker has the same prompt and model, so they differ only by sampling (temperature 1.0).
- The workers think step by step and end with `Answer: <integer>`. With a bare answer the model repeats itself even at temperature 1, and the workers never differ. The questions have one integer answer; a free-text task needs another comparison than "last integer".
- The questions were picked from 30 candidates by running one worker 8 times on each (gpt-4o-mini). That is a small sample: treat the single-worker accuracies as rough, and recheck them on another model.
- Not run against a live model in this repository's tests: the tests cover the scenarios' group sizes, the metrics on recorded traces, and the pipeline's step types.

## Output paths

Results go to `$XDG_DATA_HOME/mas/labs/agent-count/`:

- `results/results.csv`: one row per run, item and metric (from `collect_metrics`).
- `results/ci_by_run.csv`: per n and metric, the mean over the questions, the t interval across the runs, and `n` runs.
- `results/ci_by_question.csv`: the same per question.
- `results/figure-questions.png`: one line per question, the majority's success against n. Read this one first: a question one worker gets right about a third of the time climbs towards 1 as n grows, a question every worker gets wrong the same way stays at 0.
- `results/figure-agreement.png` and `results/figure-cost.png`: the averages over all questions with their intervals, and tokens per run.
