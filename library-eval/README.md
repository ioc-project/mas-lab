<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-eval

Scores MAS runs with quality metrics and LLM-as-judge prompts. It wraps the Metrics Computation Engine (MCE), an open-source set of LLM-as-judge metrics from the AGNTCY `telemetry-hub` project, for MAS traces.

Use it when you want a number for "did the system reach the goal" or "was the answer grounded" for one trace or a whole experiment.

## Quick start

```bash
uv pip install -e library-eval          # from the repository root; needs Python 3.11+
mas-lab eval --list-metrics
mas-lab eval path/to/events.jsonl --metric goal_success_rate --metric groundedness
```

Batch scoring over a benchmark output tree:

```bash
mas-lab eval path/to/experiment/ --metric goal_success_rate --recursive
```

`mas-lab eval SOURCE` flags:

| Flag | Meaning |
|------|---------|
| `--metric` | Metric to compute; repeatable. |
| `--model` | Judge model name (default `azure/gpt-4o`). |
| `--api-base` | LLM proxy base URL (default `https://api.openai.com/v1`). |
| `--api-key-env` | Environment variable holding the API key (default `OPENAI_API_KEY`). |
| `--json` | Print results as JSON. |
| `--list-metrics` | List available metrics and exit; `SOURCE` is then optional. |
| `--recursive` | Score every trace under the `SOURCE` directory. |

The package depends on `mas-lab-core`, `mas-lab-bench` and `mas-runtime` from this repository, plus the public MCE packages:

- `metrics-computation-engine`: MCE core.
- `mce-metrics-plugin`: quality metrics (GoalSuccessRate, Groundedness, ResponseCompleteness, ...).
- `deepeval`: continuous-score implementations of `answer_relevancy` and `goal_success_rate`.

## What the library provides

1. Library code (`mas.library.eval.*`): MCE integration, session entity construction, metric computation, and the `eval_metric` plugin type.
2. A CLI component (`mas.library.eval.cli`) that registers `mas-lab eval` through the `mas.lab.cli.components` entry point:

```toml
[project.entry-points."mas.lab.cli.components"]
eval = "mas.library.eval.cli:EvalCliComponent"
```

The `eval_mce` pipeline step, which scores a run into `metrics.json`, ships in `mas-library-lab` (`mas.library.lab.steps.eval.mce`) and calls into this library. `mas.library.eval.steps` holds an older CamelCase-metric step class that is not registered.

```
src/mas/library/eval/
+-- evaluator.py     EvalProvider + get_provider("mce")
+-- providers/       mce.py: built-in MCE provider (stock + registered metrics);
|                    trip_planner_gt.py, adversarial.py: sample ground-truth / adversarial providers
+-- mce/
|   +-- catalog.py       one catalog: METRIC_MAP + METRIC_REGISTRY
|   +-- runner.py        native-trace MCE/deepeval compute
|   +-- registry_api.py  SessionEntity CamelCase API
+-- metrics/         EvalMetric, plugins, prompt_metrics, stock wrappers, toy example
+-- steps/           older CamelCase-metric step class (not registered)
+-- cli/             mas-lab eval
```

## Score runs in a benchmark

Attach the `eval_mce` step to a run. Metric ids are snake_case and the judge model comes from the experiment (see [Tutorial 10](../docs/tutorials/10-evaluation-metrics/README.md)):

```yaml
# pipeline attached with `mas-lab benchmark run ... --pipeline run:post:eval.yaml`
apiVersion: mas/v1
kind: Pipeline
spec:
  steps:
    - name: eval
      type: eval_mce
      per_run: true
      in: trace
      out: metrics
      config:
        metrics: [goal_success_rate, groundedness, response_completeness]
```

## Metric names

The CLI, the `eval_mce` step `metrics:` and the `metrics.json` keys use snake_case ids. The class names used by the `SessionEntity` API (`compute_session_metrics`) are CamelCase.

| Id (CLI and YAML) | Class name |
|-------------------|------------|
| `goal_success_rate` | `GoalSuccessRate` |
| `answer_relevancy` | `AnswerRelevancy` |

`mas-lab eval --list-metrics` lists the ids. The 11 stock session metrics are `answer_relevancy`, `component_conflict_rate`, `consistency`, `context_preservation`, `goal_success_rate`, `groundedness`, `information_retention`, `intent_recognition_accuracy`, `response_completeness`, `workflow_cohesion_index` and `workflow_efficiency`. The rule-based core metrics (`ToolError`, `ToolErrorRate`, `ToolUtilizationAccuracy`, `CyclesCount`, `AgentToAgentInteractions`, `AgentToToolInteractions`) are available through the CamelCase API only.

## Register your own metric (`eval_metric` plugin)

A library registers session metrics on the MCE eval provider through the `eval_metric` plugin type (`EvalProvider.register_metric`). `eval_mce` then runs any mix of stock MCE ids and registered ids into one `metrics.json`. Each metric declares the input it needs (`unit` / `evidence`). Stock MCE ids score MAS input and output, not the raw trajectory.

```yaml
# library.yaml
types:
  - eval_metric
plugins:
  - type: eval_metric
    name: toy_echo
    module: mas.library.eval.metrics.toy
    class: ToyEchoMetric
    attributes:
      provider: mce          # default
  # A family that yields many ids from one factory:
  - type: eval_metric
    name: my_family
    factory: mas.library.example.metrics:build_metrics
    attributes:
      provider: mce
```

Rules:

- Duplicate metric ids raise at registration.
- Stock MCE session ids are `EvalMetric`s on `get_provider("mce")` (one batch per run).
- Plugins and inline `prompt_metrics` run through the same `compute_metrics` engine.
- Unknown ids error with the list of available ids.
- Metrics that share `batch_key` are computed once per run via `compute_batch`.
- A failing metric writes `{value: null, error: "..."}` for that id only.
- Optional `details` on each score is additive in `metrics.json` schema v1.

List registered ids in the `metrics:` list of an `eval_mce` step, or split families across steps with distinct `metrics_filename` values (`metrics.json`, `metrics_judge2.json`). For a one-off LLM-as-judge question, add `prompt_metrics: [{id, prompt, unit, evidence}]` to the same step without a plugin; ids must not collide with a stock or registered metric. `collect_metrics` and `metrics_to_dataframe` accept a filename, a glob or a list, and copy `details` into a JSON column.

## Programmatic use

```python
import os

from mas.library.eval.mce import build_session_from_trace, compute_session_metrics

session_entity = build_session_from_trace("path/to/events.jsonl")

results = await compute_session_metrics(      # async
    session=session_entity,
    metrics=["GoalSuccessRate", "Groundedness"],
    llm_config={
        "LLM_MODEL_NAME": "azure/gpt-4o",
        "LLM_BASE_MODEL_URL": "https://api.openai.com/v1",
        "LLM_API_KEY": os.environ["OPENAI_API_KEY"],
    },
)
```

`llm_config` is a dict as above, or an `LLMJudgeConfig` with the same three fields:

```python
from metrics_computation_engine.models.requests import LLMJudgeConfig

llm_config = LLMJudgeConfig(
    LLM_MODEL_NAME="azure/gpt-4o",
    LLM_BASE_MODEL_URL="https://api.openai.com/v1",
    LLM_API_KEY=os.environ["OPENAI_API_KEY"],
)
```

## See also

- [Tutorial 10, Evaluation metrics](../docs/tutorials/10-evaluation-metrics/README.md): how stock MCE, prompts, plugins, judge infra and trajectory wrappers fit together.
- [Tutorial 3, Run an experiment](../docs/tutorials/03-experiments-and-analysis/README.md): first MCE scoring.
- Feature example (not a sample app): [examples/mce/judge-override/](examples/mce/judge-override/)
- [summarization.md, MCE judge](../docs/manifests/summarization.md#mce-judge-model)
- [MCE documentation](https://github.com/agntcy/telemetry-hub/tree/main/metrics_computation_engine)
- [mce_metrics_plugin](https://github.com/agntcy/telemetry-hub/tree/main/metrics_computation_engine/plugins/mce_metrics_plugin)
